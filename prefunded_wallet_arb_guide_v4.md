# Prefunded Wallet Arbitrage — Implementation Guide (mcp-app-telegram)

> **Goal:** add a **prefunded wallet** arbitrage executor that buys on DEX‑A and sells on DEX‑B using router contracts (V2/V3/**V4**), with quotes, slippage, gas accounting, and clean integration with your existing admin console + MCP servers.

---

## 0) Deliverables

- `arb/prefunded_executor.py` — main entrypoint: `execute_prefunded_arb(...)`.
- `arb/quoters.py` — V2/V3/V4 quoting utilities (V4 via Quoter or Universal Router simulation).
- `arb/allowance.py` — ERC‑20 allowance/permit helpers (future: Permit2).
- `arb/costs.py` — gas & PnL accounting helpers.
- `config/routers.base.json` — DEX router/quoter registry for Base (include V2/V3/**V4** endpoints).
- Telegram command: `/arb_exec <pair|index> [amount] [slipbps] [--legA dexKey --legB dexKey]`.
- Tests: `tests/arb/test_prefunded_executor.py` (async + mocked RPC).
- Docs/help text updates.

**Non‑goals (now):** atomic flash‑swaps, MEV relays, multi‑hop pathfinding.

---

## 1) Architecture

```
Telegram (/arb_exec)
   └─> ArbService
        ├─> QuoteService (V2/V3/V4)
        ├─> AllowanceService (ERC‑20 / Permit2 later)
        ├─> ExecutionService (two swaps: A then B; supports V2/V3/V4 legs)
        └─> Accounting (gas, slippage, PnL) + Logging (SQLite)
```

- **QuoteService**: leg‑1 (tokenIn→tokenMid on A), leg‑2 (tokenMid→tokenOut on B).
- **ExecutionService**: two router calls with `amountOutMin` derived from slippage.
- **V4 note**: prefer **Universal Router v2** `V4_SWAP` command or **V4Router** periphery; avoid calling `PoolManager` directly for app‑level swaps.
- **AllowanceService**: exact approvals per swap (no infinite allowances).

---

## 2) Config & Environment

### `config/routers.base.json`
Add V2/V3 as before and include V4 entries. Example skeleton:

```json
{
  "dexes": {
    "dexA": { "type": "v2", "router": "0xROUTER_A_V2" },
    "dexB": { "type": "v2", "router": "0xROUTER_B_V2" },
    "uniV3": { "type": "v3", "swapRouter": "0xSWAP_ROUTER_V3", "quoter": "0xQUOTER_V2", "feeTiers": [500,3000,10000] },
    "uniV4": {
      "type": "v4",
      "universalRouter": "0xUNIVERSAL_ROUTER_V2",
      "v4Router": "0xV4_ROUTER_PERIPHERY",
      "poolManager": "0xPOOL_MANAGER",
      "quoter": "0xV4_QUOTER"
    }
  }
}
```

> **Important:** Always verify chain‑specific addresses from official docs before use.

### Env vars
```
ARB_SLIPPAGE_BPS=40
ARB_PROFIT_FLOOR_USDC=0.50
ARB_DEFAULT_AMOUNT_USDC=100
ARB_GAS_QUOTE_PAIR=ETH/USDC
ARB_TX_DEADLINE_SECS=120
ARB_ALLOW_V4=1
```

### Config model
```python
@dataclass
class ArbConfig:
    slippage_bps: int = int(os.getenv("ARB_SLIPPAGE_BPS", 40))
    profit_floor_usdc: Decimal = Decimal(os.getenv("ARB_PROFIT_FLOOR_USDC", "0.50"))
    default_amount_usdc: Decimal = Decimal(os.getenv("ARB_DEFAULT_AMOUNT_USDC", "100"))
    gas_quote_pair: str = os.getenv("ARB_GAS_QUOTE_PAIR", "ETH/USDC")
    tx_deadline_secs: int = int(os.getenv("ARB_TX_DEADLINE_SECS", 120))
    allow_v4: bool = os.getenv("ARB_ALLOW_V4", "0") == "1"
```

---

## 3) Registry & Abstractions

```python
# arb/routers.py
class DexType(str, Enum):
    V2 = "v2"
    V3 = "v3"
    V4 = "v4"

@dataclass
class DexEndpoint:
    name: str
    type: DexType
    # v2
    router: str | None = None
    # v3
    swap_router: str | None = None
    quoter: str | None = None
    fee_tiers: list[int] | None = None
    # v4
    universal_router: str | None = None  # Universal Router v2
    v4_router: str | None = None         # Periphery V4Router (optional)
    pool_manager: str | None = None      # PoolManager (singleton)

class RouterRegistry:
    def __init__(self, json_path: Path):
        raw = json.loads(Path(json_path).read_text())
        self._dexes: dict[str, DexEndpoint] = {}
        for name, d in raw["dexes"].items():
            self._dexes[name] = DexEndpoint(
                name=name,
                type=DexType(d["type"]),
                router=d.get("router"),
                swap_router=d.get("swapRouter"),
                quoter=d.get("quoter"),
                fee_tiers=d.get("feeTiers"),
                universal_router=d.get("universalRouter"),
                v4_router=d.get("v4Router"),
                pool_manager=d.get("poolManager")
            )
    def get(self, key: str) -> DexEndpoint:
        return self._dexes[key]
```

---

## 4) Quoting (V2/V3/V4)

```python
# arb/quoters.py  (snippets)

# V2: getAmountsOut
def quote_v2_out(w3, router_addr, amount_in_wei, path, abi_v2) -> int:
    r = w3.eth.contract(address=router_addr, abi=abi_v2)
    return r.functions.getAmountsOut(amount_in_wei, path).call()[-1]

# V3: QuoterV2 quoteExactInputSingle
def quote_v3_single(w3, quoter_addr, token_in, token_out, fee, amount_in_wei, abi_quoter_v2) -> int:
    q = w3.eth.contract(address=quoter_addr, abi=abi_quoter_v2)
    return q.functions.quoteExactInputSingle(token_in, token_out, fee, amount_in_wei, 0).call()[0]

# V4: prefer Quoter (if deployed) OR simulate via Universal Router staticcall
def quote_v4_single(w3, quoter_or_universal_addr, token_in, token_out, fee, amount_in_wei, abi_v4_quoter, abi_ur2) -> int:
    # Path A: direct V4 Quoter (simplest when available)
    if abi_v4_quoter and quoter_or_universal_addr:
        q = w3.eth.contract(address=quoter_or_universal_addr, abi=abi_v4_quoter)
        return q.functions.quoteExactInputSingle(token_in, token_out, fee, amount_in_wei, 0).call()[0]
    # Path B: Universal Router v2 `V4_SWAP` encoded call with eth_call (dry‑run)
    ur = w3.eth.contract(address=quoter_or_universal_addr, abi=abi_ur2)
    calldata = build_ur_v4_swap_calldata(token_in, token_out, fee, amount_in_wei)
    try:
        out = w3.eth.call({ "to": ur.address, "data": calldata })
        return decode_amount_out(out)
    except Exception:
        return 0

def with_slippage_min(out_wei: int, slippage_bps: int) -> int:
    return out_wei * (10_000 - slippage_bps) // 10_000
```

> **Why URv2?** Universal Router v2 can route across v2/v3/**v4** with a single command set, and is convenient for quoting by `eth_call` in environments where the V4 Quoter isn’t available on a given chain.

---

## 5) Execution (Prefunded, two legs)

### Strategy
- Leg A: BUY on chosen DEX (v2/v3/v4)
- Leg B: SELL on chosen DEX (v2/v3/v4)
- Use **tight slippage**, and **simulate** both legs prior to posting.
- Prefer **private RPC** submission when available.

### Builders
Provide one executor per family and a dispatcher:

```python
# arb/executors.py (snippets)

def swap_v2(w3, router_addr, amount_in, min_out, path, to, deadline, abi_v2, signer):
    r = w3.eth.contract(address=router_addr, abi=abi_v2)
    tx = r.functions.swapExactTokensForTokens(amount_in, min_out, path, to, deadline).build_transaction(...)
    return send_and_wait(w3, tx, signer)

def swap_v3_exact_input_single(w3, swap_router, token_in, token_out, fee, amount_in, min_out, to, deadline, abi_v3, signer):
    r = w3.eth.contract(address=swap_router, abi=abi_v3)
    params = (token_in, token_out, fee, to, deadline, amount_in, min_out, 0)
    tx = r.functions.exactInputSingle(params).build_transaction(...)
    return send_and_wait(w3, tx, signer)

def swap_v4_via_universal_router(w3, universal_router, token_in, token_out, fee, amount_in, min_out, recipient, deadline, abi_ur2, signer):
    ur = w3.eth.contract(address=universal_router, abi=abi_ur2)
    commands, inputs = build_v4_swap_commands(token_in, token_out, fee, amount_in, min_out, recipient, deadline)
    tx = ur.functions.execute(commands, inputs).build_transaction(...)
    return send_and_wait(w3, tx, signer)

def execute_leg(w3, dex: DexEndpoint, leg_kind: str, **kwargs):
    if dex.type == DexType.V2:
        return swap_v2(w3, dex.router, **kwargs)
    if dex.type == DexType.V3:
        return swap_v3_exact_input_single(w3, dex.swap_router, **kwargs)
    if dex.type == DexType.V4:
        return swap_v4_via_universal_router(w3, dex.universal_router, **kwargs)
    raise ValueError("unsupported dex type")
```

> You can also target **`v4Router`** periphery directly if you prefer not to use URv2; URv2 is convenient when you might mix versions across legs.

---

## 6) Allowances

Keep the same pattern: approve **exact** amounts before each leg. For V4/URv2 you still approve the input token for the **spending contract** (URv2 or V4Router).

```python
def ensure_allowance(w3, token, owner, spender, needed, signer): ...
```

---

## 7) Gas & PnL

As before; consider slightly higher gas estimates when using URv2 due to command encoding. Always convert gas to USDC using a live quote (WETH→USDC) to decide profitability:

```python
gas_units = APPROVE_GAS + SWAP_A_GAS + SWAP_B_GAS  # tune via estimate_gas
gas_usdc = estimate_gas_cost_usdc(w3, gas_units, eth_usdc_price)
```

---

## 8) Prefunded Arb Orchestrator

```python
# arb/prefunded_executor.py (outline)

def execute_prefunded_arb(dexA_key, dexB_key, token_in, token_mid, token_out, amount_in_wei, slipbps=None):
    # 1) quotes (A then B) using v2/v3/v4 quote helpers
    # 2) slippage mins
    # 3) allowance for leg A -> execute leg A
    # 4) allowance for leg B on received token -> execute leg B
    # 5) return tx hashes, realised deltas
    ...
```

---

## 9) Telegram integration

```
/arb_exec <pair|index> [amount] [slipbps] [--legA dexKey --legB dexKey]
```

- Resolve the pair in SQLite; default `token_in = USDC`, `token_out = USDC`.
- If `--legA/--legB` omitted, choose via policy: prefer deepest liquidity per registry.
- If either chosen DEX is V4 and `allow_v4=False`, reject with guidance to enable V4.

Reply includes both tx hashes and a reminder that this is **non‑atomic**.

---

## 10) Safety & Hooks (V4 specifics)

- **Hooks can change swap semantics** (fees, transfers, callbacks). Always **simulate** with `eth_call` before sending. Consider a tiny pre‑trade on new pools.
- **Permit2** planned: cuts approval churn and lets you set expiries.
- **MEV**: send privately where possible; tighten slippage in volatile periods.
- **Compliance**: DYOR notice in bot replies (already present).

---

## 11) Tests

- Mock V2/V3 quotes + URv2 `eth_call` return for V4 quotes.
- Executor happy path: V2→V4, V4→V2, V3→V4, etc.
- Allowance branch coverage (already enough allowance vs insufficient).
- PnL gate (skip when below floor).

---

## 12) Implementation Order (for Codex)

1. Extend `config/routers.base.json` with V4 keys (`universalRouter`, `v4Router`, `poolManager`, `quoter`).
2. Update `arb/routers.py` with `DexType.V4` + fields.
3. Add V4 quoting helpers (prefer Quoter; fallback to URv2 `eth_call`).
4. Add V4 execution via **Universal Router v2** (encode `V4_SWAP` minimal command).
5. Update main executor dispatcher to allow mixed legs V2/V3/V4.
6. Gate by `ARB_ALLOW_V4` and provide clear Telegram error if disabled.
7. Unit tests for all three families and mixed‑leg scenarios.
8. Update `/help` and README with V4 note and non‑atomic warning.

---

## 13) Addressing & Deployment Notes

- Addresses are **chain‑specific**; keep them in `config/routers.*.json` per network.
- Validate addresses on deploy with a health check that reads `code` and key view functions (e.g., UR version).
- Consider a **runtime sanity check**: simulate a $1 equivalent round‑trip on start and log the outputs per DEX family.

---

## 14) Quick reference — which contract do I call?

| Swap family | Quote path | Execute path | Pros | Cons |
|---|---|---|---|---|
| **V2** | `router.getAmountsOut` | `router.swapExactTokensForTokens` | Simple, ubiquitous | Less flexible |
| **V3** | `QuoterV2.quoteExactInputSingle` | `ISwapRouter.exactInputSingle` | Concentrated liquidity | Fee tier handling |
| **V4** | `V4 Quoter` or **URv2 `eth_call`** | **Universal Router v2** `execute(V4_SWAP)` or `V4Router` | Unified v2/v3/v4 routing, hooks support | More moving parts; hooks variability |

---

## 15) Codegen hints for GPT‑5 Codex

- Generate small, composable helpers; keep ABIs minimal (only used selectors).
- Always **parameterise addresses** via the registry; no hard‑coding in logic.
- Wrap `eth_call` simulations; treat *any* non‑zero revert data as a hard fail.
- Log compact JSON for quotes/execution `{ leg, dex, amountIn, quoteOut, minOut, txHash }`.

---

**Done.** This version adds Uniswap **v4** support via Universal Router v2 / V4Router, with quoting strategies and safety notes.
