"""A browsable wallet: the statement, read live from the ledger.

Canton's JSON Ledger API is an API, not a website.  Opening it in a browser
gives `GET / -> 404`, and because it speaks plain HTTP a browser that silently
upgrades to HTTPS gives ERR_SSL_PROTOCOL_ERROR instead.  Both are correct
behaviour and both are a dead end for a human.

So this server takes the port a human will actually type, and forwards the API
underneath it:

    daml sandbox --json-api-port 7576 --dar .daml/dist/agent-wallet-0.0.1.dar
    python -m agent_wallet.serve --port 7575 --base-url http://localhost:7576

    http://localhost:7575/                     -> the wallet statement
    http://localhost:7575/v2/state/ledger-end  -> proxied to Canton, unchanged

Every client keeps talking to 7575 and nothing else has to change.  Every page
load re-reads the ledger, so leave it open beside the demo and watch the bar
move.
"""

from __future__ import annotations

import argparse
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from . import network
from backend.canton import CantonClient, LedgerApiError

from . import statement as statement_mod
from .ledger import AGENT, OWNER, Mandate, Wallet, money, party_hint

# Anything under these belongs to Canton, not to us, and is passed straight
# through so a client pointed at this port still reaches the real ledger.
LEDGER_PREFIXES = ("/v2/", "/livez", "/readyz", "/health", "/docs", "/openapi")

# Long enough to read a statement without it reloading under you, short enough
# that it still feels live while the demo is spending beside it.
REFRESH_SECONDS = 10


class WalletView:
    """Reads the current state of every mandate the owner can see."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.client, self.target = network.client_from_env(base_url)
        self.wallet = Wallet(self.client)
        self._parties: dict[str, str] | None = None

    def parties(self) -> dict[str, str]:
        if self._parties is None:
            self._parties = self.wallet.ensure_parties()
        return self._parties

    def mandates(self) -> list[Mandate]:
        """Every mandate the owner can see, most interesting first.

        A wallet that still works beats one that has been revoked, and among
        equals the newest wins.  Without the first rule the landing page shows
        whichever mandate happens to have been created last -- after a demo run
        that is the load-test mandate, which is not what anyone came to see.
        """
        owner = self.parties()[OWNER]
        live = self.wallet.live_authority_cids(owner)
        return sorted(
            self.wallet.read_mandates(owner),
            key=lambda m: (m.authority_cid in live, m.period_start),
            reverse=True,
        )

    def statement_for(self, mandate: Mandate) -> statement_mod.Statement:
        owner = self.parties()[OWNER]
        receipts = self.wallet.read_receipts(owner, mandate.reference)
        balance = self.wallet.read_balance(owner, mandate.account_cid)
        if balance is None:
            # The mandate's account link has gone stale, which is a real state
            # worth showing rather than crashing on.
            balance = Decimal("0")
        return statement_mod.build(
            mandate=mandate,
            receipts=receipts,
            refusals=[],
            opening_balance=balance + sum(
                (r.amount for r in receipts), Decimal("0")
            ),
            closing_balance=balance,
            mandate_live=True,
            authority_live=self.wallet.authority_is_live(
                owner, mandate.authority_cid
            ),
            live=True,
        )


# -- pages --------------------------------------------------------------------

_SHELL_CSS = """
body{margin:0;background:#eceef1;color:#14181f;
font:15px/1.6 "IBM Plex Sans",ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:640px;margin:0 auto;padding:64px 24px}
h1{font-size:22px;margin:0 0 12px;letter-spacing:-.01em}
p{color:#5d6672;margin:0 0 14px}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.88em;
background:#e3e6eb;border:1px solid #d3d8df;border-radius:4px;padding:2px 6px}
a{color:#1f3a68}
@media (prefers-color-scheme:dark){
body{background:#12151a;color:#e6e9ee}
p{color:#96a0ae}
code{background:#262b33;border-color:#2a303a}
a{color:#8fb0e6}}
"""


def _shell(title: str, body: str) -> str:
    return (
        f"<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{title}</title><style>{_SHELL_CSS}</style></head>"
        f"<body><div class=wrap>{body}</div></body></html>"
    )


_PICKER_CSS = """
.switcher{max-width:1040px;margin:0 auto;padding:20px 24px 0;
display:flex;flex-wrap:wrap;gap:8px;align-items:center;
font-family:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace}
.switcher .lbl{font-size:10.5px;letter-spacing:.11em;text-transform:uppercase;
color:#5d6672;margin-right:2px}
.switcher a,.switcher span.on{display:inline-flex;align-items:baseline;gap:7px;
font-size:11.5px;padding:5px 10px;border-radius:99px;text-decoration:none;
border:1px solid #d3d8df;color:#5d6672;background:#fff;white-space:nowrap}
.switcher a:hover{border-color:#1f3a68;color:#1f3a68}
.switcher a:focus-visible{outline:2px solid #1f3a68;outline-offset:2px}
.switcher span.on{border-color:#1f3a68;color:#1f3a68;background:#e2e8f3;font-weight:600}
.switcher .amt{font-variant-numeric:tabular-nums;opacity:.75}
.switcher .more{font-size:11px;color:#8b94a1}
@media (prefers-color-scheme:dark){
.switcher .lbl{color:#96a0ae}
.switcher a,.switcher span.on{border-color:#2a303a;color:#96a0ae;background:#191d24}
.switcher a:hover{border-color:#8fb0e6;color:#8fb0e6}
.switcher span.on{border-color:#8fb0e6;color:#8fb0e6;background:#1c2634}
.switcher .more{color:#6d7683}}
"""

MAX_PICKER = 6


def _picker(mandates: list[Mandate], current: str) -> str:
    """A switcher, when a ledger has accumulated more than one mandate."""
    if len(mandates) < 2:
        return ""
    shown, rest = mandates[:MAX_PICKER], mandates[MAX_PICKER:]
    chips = []
    for m in shown:
        spent = f'<span class="amt">{money(m.spent)}/{money(m.cap)}</span>'
        if m.reference == current:
            chips.append(f'<span class="on">{m.reference} {spent}</span>')
        else:
            chips.append(f'<a href="/?ref={m.reference}">{m.reference} {spent}</a>')
    tail = (
        f'<span class="more">+{len(rest)} older</span>' if rest else ""
    )
    return (
        f"<style>{_PICKER_CSS}</style>"
        f'<nav class="switcher"><span class="lbl">Mandate</span>'
        f'{"".join(chips)}{tail}</nav>'
    )


class Handler(BaseHTTPRequestHandler):
    view: WalletView

    def log_message(self, fmt: str, *args: object) -> None:
        return  # keep the terminal clean for the demo

    def _send(self, code: int, body: str, content_type: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(code)
        # Explicit charset: without it a browser guesses, and em dashes arrive
        # as mojibake.
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    # -- proxying the real ledger ------------------------------------------

    def _is_ledger_path(self) -> bool:
        path = urlparse(self.path).path
        return any(path.startswith(prefix) for prefix in LEDGER_PREFIXES)

    def _proxy(self, method: str) -> None:
        """Forward a Canton request untouched, and its answer back untouched.

        This is what lets the wallet page sit on the port everyone already
        types while the demo, the MCP server and any curl command keep working
        against the same address.
        """
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        request = Request(self.view.base_url + self.path, data=body, method=method)
        for header in ("Content-Type", "Accept", "Authorization"):
            value = self.headers.get(header)
            if value:
                request.add_header(header, value)
        try:
            with urlopen(request, timeout=60) as response:
                payload, code = response.read(), response.status
                ctype = response.headers.get("Content-Type", "application/json")
        except HTTPError as exc:
            # A Canton rejection is a real answer -- the demo reads the Daml
            # assertMsg out of it -- so pass it through rather than masking it.
            payload, code = exc.read(), exc.code
            ctype = exc.headers.get("Content-Type", "application/json")
        except URLError as exc:
            payload = json.dumps(
                {
                    "error": "cannot reach the ledger",
                    "detail": str(exc.reason),
                    "expected_at": self.view.base_url,
                }
            ).encode()
            code, ctype = 503, "application/json"

        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:  # noqa: N802  (http.server's interface)
        if self._is_ledger_path():
            self._proxy("POST")
            return
        self._send(404, _shell("Not found", "<h1>Not found</h1>"), "text/html")

    def do_GET(self) -> None:  # noqa: N802  (http.server's interface)
        if self._is_ledger_path():
            self._proxy("GET")
            return
        route = urlparse(self.path)
        if route.path == "/healthz":
            self._send(200, "ok\n", "text/plain")
            return
        if route.path not in ("/", "/index.html", "/api/state"):
            self._send(404, _shell("Not found", "<h1>Not found</h1>"), "text/html")
            return

        # /api/state is a JSON endpoint, so it answers in JSON even when the
        # answer is "there is nothing yet" or "the ledger is down".
        wants_json = route.path == "/api/state"

        try:
            mandates = self.view.mandates()
        except LedgerApiError as exc:
            if wants_json:
                self._send(
                    503,
                    json.dumps(
                        {
                            "error": "ledger unreachable",
                            "detail": str(exc),
                            "expected_at": self.view.base_url,
                        },
                        indent=2,
                    )
                    + "\n",
                    "application/json",
                )
                return
            self._send(
                503,
                _shell(
                    "Ledger unreachable",
                    "<h1>The ledger is not reachable</h1>"
                    f"<p>{exc}</p>"
                    "<p>Start it with:</p>"
                    f"<p><code>daml sandbox --json-api-port "
                    f"{urlparse(self.view.base_url).port} "
                    "--dar .daml/dist/agent-wallet-0.0.1.dar</code></p>",
                ),
                "text/html",
            )
            return

        if not mandates:
            if wants_json:
                self._send(
                    200,
                    json.dumps({"mandates": [], "hint": "run python -m agent_wallet.demo"}, indent=2)
                    + "\n",
                    "application/json",
                )
                return
            self._send(
                200,
                _shell(
                    "No mandate yet",
                    "<h1>No mandate on this ledger yet</h1>"
                    "<p>The ledger is up, but nobody has issued a mandate. "
                    "Create one and spend against it:</p>"
                    "<p><code>python -m agent_wallet.demo</code></p>"
                    "<p>Then reload this page.</p>",
                ),
                "text/html",
            )
            return

        wanted = (parse_qs(route.query).get("ref") or [None])[0]
        mandate = next(
            (m for m in mandates if m.reference == wanted), mandates[0]
        )
        report = self.view.statement_for(mandate)

        if route.path == "/api/state":
            self._send(
                200,
                json.dumps(
                    {
                        "reference": report.reference,
                        "status": report.status,
                        "cap": str(report.cap),
                        "spent": str(report.spent),
                        "remaining": str(report.remaining),
                        "balance": str(report.closing_balance),
                        "charges": len(report.receipts),
                        "allowed_payees": [
                            party_hint(p) for p in report.allowed_payees
                        ],
                    },
                    indent=2,
                )
                + "\n",
                "application/json",
            )
            return

        page = statement_mod.render_html(report)
        head = (
            f'<!doctype html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta http-equiv="refresh" content="{REFRESH_SECONDS}">'
            f"</head><body>"
        )
        self._send(
            200,
            head + _picker(mandates, mandate.reference) + page + "</body></html>",
            "text/html",
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Serve the agent wallet statement, read live from the ledger"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=7575,
        help="port to serve the wallet on (default 7575, the one people type)",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:7576",
        help="where the real Canton JSON Ledger API is listening",
    )
    args = parser.parse_args()

    if args.base_url.rstrip("/").endswith(f":{args.port}"):
        raise SystemExit(
            f"--port {args.port} and --base-url {args.base_url} are the same "
            "address; the proxy would loop.\n"
            "Start the sandbox on another port, e.g. "
            "`daml sandbox --json-api-port 7576 ...`, or pass a different --port."
        )

    Handler.view = WalletView(args.base_url)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Wallet statement:  http://localhost:{args.port}")
    print(f"Ledger API:        http://localhost:{args.port}/v2/...  ->  {args.base_url}")
    print(f"Refreshing every {REFRESH_SECONDS}s. Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
