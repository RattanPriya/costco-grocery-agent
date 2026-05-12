from __future__ import annotations

import html
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from grocery_agent.agent import GroceryAgent
from grocery_agent.costco import MockCostcoClient
from grocery_agent.models import Cart, CartStatus
from grocery_agent.security import ApprovalTokenError, ApprovalTokenSigner
from grocery_agent.storage import JsonStore


def run(host: str = "127.0.0.1", port: int = 8765) -> None:
    store = JsonStore(_data_path())
    signer = ApprovalTokenSigner(_approval_secret())
    server = ThreadingHTTPServer((host, port), _handler(store, signer))
    print(f"Costco grocery review app listening on http://{host}:{port}")
    server.serve_forever()


def _handler(store: JsonStore, signer: ApprovalTokenSigner) -> type[BaseHTTPRequestHandler]:
    agent = GroceryAgent(store=store, costco=MockCostcoClient())

    class GroceryReviewHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._send_text("ok")
                return
            if parsed.path in {"/", "/cart"}:
                self._send_html(_review_page(agent, signer))
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length", "0"))
            params = parse_qs(self.rfile.read(length).decode("utf-8"))
            token = params.get("token", [""])[0]
            if parsed.path == "/approve":
                self._handle_approval(token)
                return
            if parsed.path == "/reject":
                self._handle_rejection(token, params.get("reason", ["Rejected from phone review."])[0])
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _handle_approval(self, token: str) -> None:
            try:
                payload = signer.verify(token, "approve")
                cart = agent.approve_cart(payload.cart_id, approver="phone", statement="I approve this Costco cart for checkout review.")
            except (ApprovalTokenError, ValueError, RuntimeError) as exc:
                self._send_html(_message_page("Approval failed", str(exc)), HTTPStatus.BAD_REQUEST)
                return
            self._send_html(_message_page("Cart approved", f"Cart {cart.id} is approved. Final purchase still requires checkout review."))

        def _handle_rejection(self, token: str, reason: str) -> None:
            try:
                payload = signer.verify(token, "reject")
                cart = agent.reject_cart(payload.cart_id, reason=reason)
            except (ApprovalTokenError, ValueError, RuntimeError) as exc:
                self._send_html(_message_page("Rejection failed", str(exc)), HTTPStatus.BAD_REQUEST)
                return
            self._send_html(_message_page("Cart rejected", f"Cart {cart.id} was rejected."))

        def _send_text(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def _send_html(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

    return GroceryReviewHandler


def _review_page(agent: GroceryAgent, signer: ApprovalTokenSigner) -> str:
    cart = agent.store.latest_cart()
    if cart is None:
        return _shell("No Cart", "<main><h1>No cart ready</h1><p>No Costco cart has been prepared yet.</p></main>")
    approve_token = signer.create(cart.id, "approve")
    reject_token = signer.create(cart.id, "reject")
    return _shell(
        "Costco Cart Review",
        f"""
        <main>
          <p class="eyebrow">Costco grocery agent</p>
          <h1>Review cart</h1>
          {_status_badge(cart)}
          <section>
            <h2>Items</h2>
            {_items_html(cart)}
          </section>
          {_out_of_stock_html(cart)}
          <section>
            <h2>Total</h2>
            <p class="total">${cart.total_cost:.2f}</p>
            <p>{html.escape(cart.selected_fulfillment or "Fulfillment not selected")}</p>
          </section>
          <section>
            <h2>Decisions</h2>
            {_decisions_html(cart)}
          </section>
          {_action_forms(cart, approve_token, reject_token)}
        </main>
        """,
    )


def _status_badge(cart: Cart) -> str:
    return f'<p class="badge">{html.escape(str(cart.status))}</p>'


def _items_html(cart: Cart) -> str:
    if not cart.items:
        return "<p>No items in cart.</p>"
    rows = []
    for item in cart.items:
        flags = f"<p class='flags'>{html.escape(', '.join(item.flags))}</p>" if item.flags else ""
        rows.append(
            f"""
            <article class="line-item">
              <div>
                <h3>{html.escape(item.product.name)}</h3>
                <p>{html.escape(item.reason)}</p>
                {flags}
              </div>
              <strong>x{item.quantity} · ${item.line_total:.2f}</strong>
            </article>
            """
        )
    return "\n".join(rows)


def _out_of_stock_html(cart: Cart) -> str:
    if not cart.out_of_stock:
        return ""
    rows = []
    for item in cart.out_of_stock:
        substitutions = ", ".join(product.name for product in item.suggested_substitutions) or "No substitution found"
        rows.append(f"<li><strong>{html.escape(item.requested_item)}</strong>: {html.escape(item.reason)} {html.escape(substitutions)}</li>")
    return f"<section><h2>Needs review</h2><ul>{''.join(rows)}</ul></section>"


def _decisions_html(cart: Cart) -> str:
    if not cart.decision_log:
        return "<p>No decision log entries.</p>"
    return "<ul>" + "".join(f"<li>{html.escape(entry.action)}: {html.escape(entry.detail)}</li>" for entry in cart.decision_log) + "</ul>"


def _action_forms(cart: Cart, approve_token: str, reject_token: str) -> str:
    disabled = "disabled" if cart.status is not CartStatus.REVIEW_READY else ""
    return f"""
      <section class="actions">
        <form method="post" action="/approve">
          <input type="hidden" name="token" value="{html.escape(approve_token)}">
          <button {disabled}>Approve cart</button>
        </form>
        <form method="post" action="/reject">
          <input type="hidden" name="token" value="{html.escape(reject_token)}">
          <input name="reason" placeholder="Reason">
          <button class="secondary" {disabled}>Reject</button>
        </form>
        <p>Approval here authorizes checkout review only. Final order placement still requires explicit purchase approval.</p>
      </section>
    """


def _message_page(title: str, message: str) -> str:
    return _shell(title, f"<main><h1>{html.escape(title)}</h1><p>{html.escape(message)}</p><p><a href='/cart'>Back to cart</a></p></main>")


def _shell(title: str, body: str) -> str:
    return f"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body {{ margin: 0; background: #f7f8f5; color: #17201b; }}
    main {{ max-width: 760px; margin: 0 auto; padding: 24px 16px 48px; }}
    h1 {{ font-size: 32px; margin: 0 0 12px; }}
    h2 {{ font-size: 18px; margin: 28px 0 12px; }}
    h3 {{ font-size: 16px; margin: 0 0 6px; }}
    p {{ line-height: 1.45; }}
    .eyebrow {{ margin: 0 0 4px; color: #496253; font-weight: 700; text-transform: uppercase; font-size: 12px; }}
    .badge {{ display: inline-block; padding: 4px 8px; border-radius: 6px; background: #dce8df; font-size: 13px; }}
    .line-item {{ display: flex; gap: 16px; justify-content: space-between; padding: 14px 0; border-top: 1px solid #d8ddd7; }}
    .line-item strong {{ white-space: nowrap; }}
    .flags {{ color: #8a4700; font-weight: 600; }}
    .total {{ font-size: 28px; font-weight: 800; margin: 0; }}
    .actions {{ display: grid; gap: 12px; margin-top: 28px; }}
    button {{ width: 100%; border: 0; border-radius: 8px; background: #176b4d; color: white; padding: 14px 16px; font-weight: 800; font-size: 16px; }}
    button.secondary {{ background: #6e2f2f; }}
    button:disabled {{ background: #a9aea9; }}
    input {{ width: 100%; box-sizing: border-box; padding: 12px; border: 1px solid #bbc2bb; border-radius: 8px; margin-bottom: 8px; }}
    a {{ color: #176b4d; }}
  </style>
</head>
<body>
{body}
</body>
</html>
"""


def _data_path() -> Path:
    return Path(os.environ.get("GROCERY_AGENT_DATA", ".grocery_agent/data.json"))


def _approval_secret() -> str:
    secret = os.environ.get("GROCERY_AGENT_APPROVAL_SECRET")
    if secret:
        return secret
    if os.environ.get("GROCERY_AGENT_ENV") == "production":
        raise RuntimeError("GROCERY_AGENT_APPROVAL_SECRET is required in production.")
    return "dev-only-change-me"


if __name__ == "__main__":
    run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8765")))
