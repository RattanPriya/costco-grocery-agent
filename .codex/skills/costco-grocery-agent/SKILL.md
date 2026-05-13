---
name: costco-grocery-agent
description: Use when working on this repo's Costco Same Day grocery agent, especially Telegram commands, live Chrome automation, product-rule mappings, cart text extraction, checkout review, or approval/place-order safety gates.
---

# Costco Grocery Agent

## Operating Posture

This project automates a real shopping surface. Keep the boundary crisp:

- It may open Costco Same Day in Chrome using saved browser cookies.
- It may add exact mapped products to the real cart.
- It may extract and send cart/checkout text to Telegram.
- It must not store Costco passwords.
- It must not click the real `Place order` button unless the user gives a final explicit approval after checkout details are visible.

Prefer clear failure messages over generic browser errors. When Costco UI changes, inspect the live DOM/text before guessing.

## Core Workflow

For Telegram-driven real ordering:

1. Run the live bot with `python3 -m grocery_agent.cli telegram-costco-bot`.
2. User sends `/grocery item one, item two`.
3. Bot opens `https://sameday.costco.com/store/costco/storefront` in Google Chrome.
4. Preflight verifies Same Day is signed in.
5. Each item resolves through `ProductRule`; do not add unmapped products.
6. Bot sends build result plus `Current Costco cart` text from the live cart drawer.
7. Bot opens checkout review if available and sends total, address, payment, delivery window, item count, and approval buttons.
8. Only `Approve and place order` may click the real Costco `Place order` button.

## Fragile Costco UI Lessons

- The floating cart button text changes. It may say `View cart`, or savings text like `Saving $1.50 3`. Prefer stable selectors such as `#floating-cart-button` or `[data-testid="floating-cart-button"]`.
- Plain `.click()` may not trigger Costco controls. Use pointer/mouse event dispatch in the Chrome adapter.
- Product pages may need a few seconds after navigation before product rows render. Wait for `Results for` and/or the preferred product text.
- A missing checkout button can mean the cart is below Costco's minimum. Detect messages like `$35 Min. to checkout` or `Add $X more` and report that directly.
- The cart drawer text is often more useful than a link. Send extracted text to Telegram even when checkout cannot proceed.

## Product Rules

Product rules are intentionally conservative. If the bot says `Needs product mapping`, inspect live Costco search results and add an exact `ProductRule`.

Recent known mappings:

- `strawberry` / `strawberries` -> `Kirkland Signature Organic Strawberries, 4 lbs`
- `blueberries` -> `Blueberries, 18 oz`
- `banana` / `bananas` -> `Bananas, 3 lbs`
- `apple` / `apples` -> `Organic Fuji Apples, 4 lbs`
- `lamb chop` / `lamb chops` -> `Kirkland Signature Lamb Loin Chops, Australian`
- `chicken thighs` -> `Kirkland Signature Fresh Boneless Skinless Chicken Thighs`
- `olive oil` -> `Kirkland Signature, Organic Extra Virgin Olive Oil, 2 L`

Normalize user item names for punctuation and simple singular/plural variants, but do not infer brand or variant preferences beyond remembered rules.

## Validation

After changes, run:

```bash
python3 -m unittest discover -s tests
```

For live behavior, inspect Chrome through `AppleScriptChromeSession` and verify:

- current URL is `sameday.costco.com`
- `preflight.ok` is true
- cart text extraction works
- checkout review reports either real checkout details or a useful blocker

Keep tests updated for every live Costco failure mode discovered during use.
