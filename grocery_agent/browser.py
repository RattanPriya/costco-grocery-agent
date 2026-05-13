from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from typing import Protocol

from grocery_agent.models import BrowserPageState


class BrowserAutomationError(RuntimeError):
    pass


class BrowserSession(Protocol):
    def current_state(self) -> BrowserPageState:
        raise NotImplementedError

    def open_url(self, url: str) -> BrowserPageState:
        raise NotImplementedError

    def navigate(self, url: str) -> BrowserPageState:
        raise NotImplementedError

    def click_button(self, text: str | None = None, aria: str | None = None) -> BrowserPageState:
        raise NotImplementedError

    def click_button_containing(self, text: str | None = None, aria: str | None = None) -> BrowserPageState:
        raise NotImplementedError

    def click_button_near_text(self, nearby_text: str, button_text: str) -> BrowserPageState:
        raise NotImplementedError

    def click_selector(self, selector: str) -> BrowserPageState:
        raise NotImplementedError

    def set_text_input(self, selector: str, value: str) -> BrowserPageState:
        raise NotImplementedError


@dataclass(slots=True)
class FakeBrowserSession:
    states: list[BrowserPageState]
    actions: list[str] = field(default_factory=list)

    def current_state(self) -> BrowserPageState:
        if not self.states:
            raise BrowserAutomationError("No fake browser states configured.")
        return self.states[-1]

    def open_url(self, url: str) -> BrowserPageState:
        self.actions.append(f"open_url:{url}")
        state = self.current_state()
        return BrowserPageState(url=url, title=state.title, body_text=state.body_text, buttons=state.buttons, inputs=state.inputs, dialogs=state.dialogs)

    def navigate(self, url: str) -> BrowserPageState:
        self.actions.append(f"navigate:{url}")
        state = self.current_state()
        return BrowserPageState(url=url, title=state.title, body_text=state.body_text, buttons=state.buttons, inputs=state.inputs, dialogs=state.dialogs)

    def click_button(self, text: str | None = None, aria: str | None = None) -> BrowserPageState:
        label = aria or text
        if label and label not in _fake_button_labels(self.current_state()):
            raise BrowserAutomationError(f"Button not found: {label}")
        self.actions.append(f"click:{label}")
        return self.current_state()

    def click_button_containing(self, text: str | None = None, aria: str | None = None) -> BrowserPageState:
        label = aria or text
        if label and not any(label in button for button in _fake_button_labels(self.current_state())):
            raise BrowserAutomationError(f"Button not found: {label}")
        self.actions.append(f"click_contains:{label}")
        return self.current_state()

    def click_button_near_text(self, nearby_text: str, button_text: str) -> BrowserPageState:
        self.actions.append(f"click_near:{nearby_text}:{button_text}")
        return self.current_state()

    def click_selector(self, selector: str) -> BrowserPageState:
        self.actions.append(f"click_selector:{selector}")
        return self.current_state()

    def set_text_input(self, selector: str, value: str) -> BrowserPageState:
        self.actions.append(f"set_text:{selector}:{value}")
        return self.current_state()


def _fake_button_labels(state: BrowserPageState) -> list[str]:
    return [label.replace("\n", " ").strip() for label in state.buttons]


class AppleScriptChromeSession:
    """Minimal Chrome adapter for Costco Same Day.

    This adapter intentionally exposes semantic primitives only. It does not type
    passwords, read hidden form values, or click arbitrary coordinates.
    """

    def __init__(self, settle_seconds: float = 3.0, target_url_substring: str | None = None) -> None:
        self.settle_seconds = settle_seconds
        self.target_url_substring = target_url_substring

    def current_state(self) -> BrowserPageState:
        return _state_from_json(self._execute_javascript(_js_state()))

    def open_url(self, url: str) -> BrowserPageState:
        _run_osascript(f'tell application "Google Chrome" to open location {json.dumps(url)}')
        self._settle()
        return self.current_state()

    def navigate(self, url: str) -> BrowserPageState:
        js = f"location.href={json.dumps(url)}"
        self._execute_javascript(js)
        self._settle()
        return self.current_state()

    def click_button(self, text: str | None = None, aria: str | None = None) -> BrowserPageState:
        if not text and not aria:
            raise ValueError("text or aria is required")
        predicate = _button_predicate(text=text, aria=aria, exact=True)
        clicked = self._execute_javascript(_click_button_js(predicate))
        if clicked != "true":
            raise BrowserAutomationError(f"Button not found: {aria or text}")
        self._settle()
        return self.current_state()

    def click_button_containing(self, text: str | None = None, aria: str | None = None) -> BrowserPageState:
        if not text and not aria:
            raise ValueError("text or aria is required")
        predicate = _button_predicate(text=text, aria=aria, exact=False)
        clicked = self._execute_javascript(_click_button_js(predicate))
        if clicked != "true":
            raise BrowserAutomationError(f"Button not found: {aria or text}")
        self._settle()
        return self.current_state()

    def click_button_near_text(self, nearby_text: str, button_text: str) -> BrowserPageState:
        clicked = self._execute_javascript(_click_button_near_text_js(nearby_text, button_text))
        if clicked != "true":
            raise BrowserAutomationError(f"Button {button_text!r} not found near {nearby_text!r}")
        self._settle()
        return self.current_state()

    def click_selector(self, selector: str) -> BrowserPageState:
        clicked = self._execute_javascript(_click_selector_js(selector))
        if clicked != "true":
            raise BrowserAutomationError(f"Element not found: {selector}")
        self._settle()
        return self.current_state()

    def set_text_input(self, selector: str, value: str) -> BrowserPageState:
        js = (
            "const input=document.querySelector("
            + json.dumps(selector)
            + "); if(!input) false; else { input.focus(); input.value="
            + json.dumps(value)
            + '; input.dispatchEvent(new Event("input",{bubbles:true}));'
            + ' input.dispatchEvent(new Event("change",{bubbles:true})); true; }'
        )
        changed = self._execute_javascript(js)
        if changed != "true":
            raise BrowserAutomationError(f"Input not found: {selector}")
        self._settle()
        return self.current_state()

    def _execute_javascript(self, js: str) -> str:
        if not self.target_url_substring:
            return _run_osascript(f'tell application "Google Chrome" to execute active tab of front window javascript {json.dumps(js)}')
        script = _targeted_javascript_script(self.target_url_substring, js)
        result = _run_osascript(script)
        if result == "__NO_MATCHING_TAB__":
            raise BrowserAutomationError(f"No Chrome tab matched URL substring: {self.target_url_substring}")
        return result

    def _settle(self) -> None:
        if self.settle_seconds > 0:
            time.sleep(self.settle_seconds)


def _run_osascript(script: str) -> str:
    result = subprocess.run(["osascript", "-e", script], check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _state_from_json(raw: str) -> BrowserPageState:
    data = json.loads(raw)
    return BrowserPageState(
        url=data["url"],
        title=data["title"],
        body_text=data.get("body", ""),
        buttons=data.get("buttons", []),
        inputs=data.get("inputs", []),
        dialogs=data.get("dialogs", []),
    )


def _js_state() -> str:
    return (
        "JSON.stringify({"
        "url:location.href,"
        "title:document.title,"
        "body:document.body.innerText.slice(0,12000),"
        "buttons:[...document.querySelectorAll('button')].filter(b=>!!(b.offsetWidth||b.offsetHeight||b.getClientRects().length)).map(b=>(b.innerText||b.getAttribute('aria-label')||'').trim()).filter(Boolean),"
        "inputs:[...document.querySelectorAll('input,textarea,select')].filter(e=>!!(e.offsetWidth||e.offsetHeight||e.getClientRects().length)).map(e=>(e.placeholder||e.getAttribute('aria-label')||e.value||e.id||e.name||'').trim()).filter(Boolean),"
        "dialogs:[...document.querySelectorAll('[role=dialog],dialog,[aria-modal=true]')].filter(e=>!!(e.offsetWidth||e.offsetHeight||e.getClientRects().length)).map(e=>e.innerText.slice(0,3000))"
        "})"
    )


def _button_predicate(text: str | None, aria: str | None, exact: bool) -> str:
    field = "_buttonLabel(b)"
    value = json.dumps(aria or text)
    if exact:
        return f"{field}==={value}"
    return f"{field}.includes({value})"


def _click_button_script(predicate: str) -> str:
    return f'tell application "Google Chrome" to execute active tab of front window javascript {json.dumps(_click_button_js(predicate))}'


def _click_button_js(predicate: str) -> str:
    return f"""
(() => {{
const _norm=s=>(s||'').replace(/\\s+/g,' ').trim();
const _buttonLabel=b=>_norm([b.innerText,b.textContent,b.getAttribute('aria-label')].filter(Boolean).join(' '));
const b=[...document.querySelectorAll('button,[role=button],a')].find(b=>{predicate});
if(!b) return false;
b.scrollIntoView({{block:'center'}});
for (const type of ['pointerdown','mousedown','mouseup','click']) {{
  b.dispatchEvent(new MouseEvent(type,{{bubbles:true,cancelable:true,view:window}}));
}}
return true;
}})();
"""


def _click_button_near_text_script(nearby_text: str, button_text: str) -> str:
    return f'tell application "Google Chrome" to execute active tab of front window javascript {json.dumps(_click_button_near_text_js(nearby_text, button_text))}'


def _click_button_near_text_js(nearby_text: str, button_text: str) -> str:
    js = """
(() => {
const wantedText = %s;
const wantedButton = %s;
const visible = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
const textMatches = e => visible(e) && (e.innerText || '').includes(wantedText);
const candidates = [...document.querySelectorAll('a,div,span,h1,h2,h3,h4,p,li')].filter(textMatches);
for (const candidate of candidates) {
  let node = candidate;
  for (let depth = 0; node && depth < 10; depth += 1, node = node.parentElement) {
    const buttons = [...node.querySelectorAll('button')].filter(visible);
    const button = buttons.find(b => (b.innerText || b.getAttribute('aria-label') || '').trim() === wantedButton);
    if (button && (node.innerText || '').includes(wantedText)) {
      button.click();
      return true;
    }
  }
}
return false;
})();
""" % (json.dumps(nearby_text), json.dumps(button_text))
    return js


def _click_selector_js(selector: str) -> str:
    return f"""
(() => {{
const element = document.querySelector({json.dumps(selector)});
if (!element) return false;
element.scrollIntoView({{block:'center'}});
for (const type of ['pointerdown','mousedown','mouseup','click']) {{
  element.dispatchEvent(new MouseEvent(type,{{bubbles:true,cancelable:true,view:window}}));
}}
return true;
}})();
"""


def _targeted_javascript_script(url_substring: str, js: str) -> str:
    return f"""
tell application "Google Chrome"
  repeat with w from (count of windows) to 1 by -1
    repeat with t from (count of tabs of window w) to 1 by -1
      set tabUrl to URL of tab t of window w
      if tabUrl contains {json.dumps(url_substring)} then
        set active tab index of window w to t
        return execute tab t of window w javascript {json.dumps(js)}
      end if
    end repeat
  end repeat
  return "__NO_MATCHING_TAB__"
end tell
"""
