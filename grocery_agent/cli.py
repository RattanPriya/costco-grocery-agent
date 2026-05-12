from __future__ import annotations

import argparse
import os
from datetime import date
from pathlib import Path

from grocery_agent.agent import GroceryAgent
from grocery_agent.browser import AppleScriptChromeSession, BrowserAutomationError
from grocery_agent.costco import MockCostcoClient
from grocery_agent.costco_sameday import CostcoSameDayBrowserAgent, SafetyGateError, remember_product_rule
from grocery_agent.models import GroceryProfile, PantryEstimate, ProductRule
from grocery_agent.scheduler import BiweeklySundayScheduler
from grocery_agent.storage import JsonStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Personal Costco grocery-ordering agent")
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("init-demo", help="Create a demo household profile")
    cart_parser = subcommands.add_parser("cart", help="Generate a cart from a comma-separated list")
    cart_parser.add_argument("items")
    subcommands.add_parser("review", help="Review the latest cart")
    approve_parser = subcommands.add_parser("approve", help="Approve the latest cart")
    approve_parser.add_argument("--approver", default="user")
    reject_parser = subcommands.add_parser("reject", help="Reject the latest cart")
    reject_parser.add_argument("--reason", default="Rejected by user.")
    edit_parser = subcommands.add_parser("edit-qty", help="Edit a product quantity by SKU")
    edit_parser.add_argument("sku")
    edit_parser.add_argument("quantity", type=int)
    subcommands.add_parser("place-order", help="Place approved mocked Costco order")
    proactive_parser = subcommands.add_parser("proactive", help="Prepare cart if every-other-Sunday schedule is due")
    proactive_parser.add_argument("--today", default=date.today().isoformat())
    proactive_parser.add_argument("--anchor-sunday", default="2026-05-10")
    rule_parser = subcommands.add_parser("remember-rule", help="Remember an exact Costco Same Day product mapping")
    rule_parser.add_argument("item")
    rule_parser.add_argument("search_query")
    rule_parser.add_argument("product_name")
    rule_parser.add_argument("--quantity", type=int, default=1)
    rule_parser.add_argument("--max-price", type=float)
    policy_parser = subcommands.add_parser("set-policy", help="Set checkout policy defaults")
    policy_parser.add_argument("--address")
    policy_parser.add_argument("--zip")
    policy_parser.add_argument("--tip", type=float)
    policy_parser.add_argument("--max-total", type=float)
    browser_parser = subcommands.add_parser("browser-preflight", help="Check active Chrome Costco Same Day state")
    browser_parser.add_argument("--strict", action="store_true")
    browser_parser.add_argument("--target-url-substring", default="sameday.costco.com", help="Chrome tab URL substring to target")
    build_parser = subcommands.add_parser("browser-build-cart", help="Build a Costco Same Day cart in active Chrome")
    build_parser.add_argument("items", help="Comma-separated household grocery item names")
    build_parser.add_argument("--settle-seconds", type=float, default=4.0, help="Seconds to wait after each browser action")
    build_parser.add_argument("--target-url-substring", default="sameday.costco.com", help="Chrome tab URL substring to target")
    web_parser = subcommands.add_parser("serve-review", help="Run phone-friendly cart review web app")
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=8765)

    args = parser.parse_args()
    store = JsonStore(_data_path())
    agent = GroceryAgent(store=store, costco=MockCostcoClient())

    if args.command == "init-demo":
        profile = GroceryProfile()
        profile.preferences.preferred_brands = {"milk": ["Kirkland"], "eggs": ["Kirkland"], "baby": ["Huggies", "Kirkland"]}
        profile.preferences.always_buy = ["milk", "eggs", "bananas"]
        profile.preferences.never_buy = ["shellfish", "yellow onions"]
        profile.preferences.substitution_preferences = {"onions": ["red onions"]}
        profile.preferences.checkout_policy.delivery_address = "1439 Tarrytown Street"
        profile.preferences.checkout_policy.delivery_zip = "94402"
        profile.preferences.checkout_policy.preferred_tip = 0.0
        profile.preferences.product_rules = {
            "strawberries": ProductRule("strawberries", "strawberries fresh", "Kirkland Signature Organic Strawberries, 4 lbs"),
            "raspberries": ProductRule("raspberries", "raspberries", "Raspberries, 12 oz"),
            "watermelon": ProductRule("watermelon", "watermelon", "Seedless Watermelon"),
            "oranges": ProductRule("oranges", "naval oranges", "Naval Oranges, 8 lbs"),
            "onions": ProductRule("onions", "red onions", "Red Onions, 5 lbs", notes="Household prefers red onions; avoid yellow onions."),
            "tomatoes": ProductRule("tomatoes", "tomatoes", "Campari Tomatoes, 2 lbs"),
            "spinach": ProductRule("spinach", "spinach", "Organic Baby Spinach, 1 lb"),
            "carrots": ProductRule("carrots", "carrots", "Organic Carrots, 6 lbs"),
            "cherries": ProductRule("cherries", "cherries", "Sweet Red Cherries, 2 lbs"),
            "paneer": ProductRule("paneer", "paneer", "Paneer Cheese, 2 x 14 oz"),
            "olive oil": ProductRule("olive oil", "olive oil", "Kirkland Signature, Organic Extra Virgin Olive Oil, 2 L"),
            "scotch brite pads": ProductRule("scotch brite pads", "scotch brite pads", "Scotch-Brite Zero Scratch Sponge, 24-count"),
        }
        profile.pantry = [
            PantryEstimate("milk", "milk", cadence_days=14, last_purchased=None, usual_quantity=1),
            PantryEstimate("eggs", "eggs", cadence_days=21, last_purchased=None, usual_quantity=1),
            PantryEstimate("paper towels", "household", cadence_days=45, last_purchased=None, usual_quantity=1),
        ]
        store.save_profile(profile)
        print(f"Demo profile saved to {_data_path()}")
    elif args.command == "cart":
        cart = agent.generate_cart(args.items.split(","))
        print(agent.review_summary(cart))
    elif args.command == "review":
        print(agent.review_summary())
    elif args.command == "approve":
        cart = agent.approve_cart(approver=args.approver)
        print(f"Approved cart {cart.id}.")
    elif args.command == "reject":
        cart = agent.reject_cart(reason=args.reason)
        print(f"Rejected cart {cart.id}.")
    elif args.command == "edit-qty":
        cart = agent.edit_quantity(None, args.sku, args.quantity)
        print(agent.review_summary(cart))
    elif args.command == "place-order":
        order = agent.place_order()
        print(f"Placed mocked Costco order {order.order_id} for ${order.total_cost:.2f}.")
    elif args.command == "proactive":
        scheduler = BiweeklySundayScheduler(anchor_sunday=date.fromisoformat(args.anchor_sunday))
        cart = scheduler.prepare_if_due(agent, date.fromisoformat(args.today))
        if cart is None:
            print("No proactive cart due today.")
        else:
            print(agent.review_summary(cart))
    elif args.command == "remember-rule":
        profile = store.load_profile()
        remember_product_rule(
            profile,
            ProductRule(
                canonical_item=args.item,
                search_query=args.search_query,
                preferred_product_name=args.product_name,
                quantity=args.quantity,
                max_price=args.max_price,
            ),
        )
        store.save_profile(profile)
        print(f"Remembered {args.item} -> {args.product_name}")
    elif args.command == "set-policy":
        profile = store.load_profile()
        policy = profile.preferences.checkout_policy
        if args.address is not None:
            policy.delivery_address = args.address
        if args.zip is not None:
            policy.delivery_zip = args.zip
        if args.tip is not None:
            policy.preferred_tip = args.tip
        if args.max_total is not None:
            policy.max_total_without_reapproval = args.max_total
        store.save_profile(profile)
        print("Checkout policy saved.")
    elif args.command == "browser-preflight":
        try:
            profile = store.load_profile()
            report = CostcoSameDayBrowserAgent(AppleScriptChromeSession(target_url_substring=args.target_url_substring), profile).preflight()
        except BrowserAutomationError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"OK: {report.ok}")
        print(f"URL: {report.url}")
        print(f"Signed in: {report.signed_in}")
        print(f"Delivery address: {report.delivery_address}")
        print(f"Cart count: {report.cart_count}")
        for issue in report.issues:
            print(f"Issue: {issue}")
        if args.strict and not report.ok:
            raise SystemExit(1)
    elif args.command == "browser-build-cart":
        try:
            profile = store.load_profile()
            browser_agent = CostcoSameDayBrowserAgent(
                AppleScriptChromeSession(settle_seconds=args.settle_seconds, target_url_substring=args.target_url_substring),
                profile,
            )
            result = browser_agent.build_cart([item.strip() for item in args.items.split(",") if item.strip()])
        except (BrowserAutomationError, SafetyGateError) as exc:
            raise SystemExit(str(exc)) from exc
        print("Added:")
        for item in result.added:
            print(f"- {item}")
        if result.missing_rules:
            print("Needs review:")
            for item in result.missing_rules:
                print(f"- {item}")
    elif args.command == "serve-review":
        from grocery_agent.web_app import run

        run(host=args.host, port=args.port)


def _data_path() -> Path:
    return Path(os.environ.get("GROCERY_AGENT_DATA", ".grocery_agent/data.json"))


if __name__ == "__main__":
    main()
