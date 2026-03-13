import inspect
import re
import textwrap
import os
import random
import string
import json
import base64
import hashlib
import requests
from urllib.parse import urlparse
from datetime import datetime, timezone
import ba_ws_sdk.streaming as streaming
from testui.support.testui_driver import TestUIDriver

test_variables = {}
driver: dict[str, TestUIDriver] = {}
run_test_id = ""

MEMORY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page_memory")


def _parse_url_parts(url: str):
    parsed = urlparse(url)
    domain = parsed.netloc or parsed.hostname or "unknown"
    domain = domain.removeprefix("www.")
    path = parsed.path.rstrip("/") or "/"
    return domain, path


def _memory_file_path(domain: str) -> str:
    return os.path.join(MEMORY_DIR, f"{domain}.json")


def _load_domain_memory(domain: str) -> dict:
    path = _memory_file_path(domain)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"domain": domain, "updated_at": None, "pages": {}}


def _save_domain_memory(domain: str, data: dict):
    os.makedirs(MEMORY_DIR, exist_ok=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(_memory_file_path(domain), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _generate_memory_id() -> str:
    return "m_" + hashlib.sha256(
        (datetime.now(timezone.utc).isoformat() + os.urandom(4).hex()).encode()
    ).hexdigest()[:10]


def _get_memories_for_url(url: str) -> list:
    domain, path = _parse_url_parts(url)
    data = _load_domain_memory(domain)
    results = []
    page_data = data.get("pages", {}).get(path, {})
    results.extend(page_data.get("memories", []))
    if path != "/":
        root_data = data.get("pages", {}).get("/", {})
        results.extend(root_data.get("memories", []))
    return results


def test_function(_run_test_id='1'): return "test function works"

def clean_html(html_content):
    for tag in ['script', 'style', 'svg']:
        html_content = re.sub(rf'<{tag}[^>]*>.*?</{tag}>', '', html_content, flags=re.DOTALL)
    return html_content

def stop_all_drivers():
    global driver
    for run_id, drv in list(driver.items()):
        try:
            streaming.stop_stream(run_id)
        except Exception:
            pass
        try:
            drv.quit()
            print(f"✅ Driver '{run_id}' stopped.")
        except Exception as e:
            print(f"⚠️ Error stopping driver '{run_id}': {e}")
    driver.clear()
    print("🗑️ All drivers stopped and entries cleared.")

def log_function_definition(fn, *args, **kwargs):
    """
    Writes a transformed version of the function's source code to `tests/function_calls{_run_test_id}.py`.
    Removes def line, triple-quoted docstrings, global lines, return lines, calls to helper introspection funcs,
    replaces param=param with the actual passed value, and replaces driver[_run_test_id] with driver.
    """
    source = inspect.getsource(fn)
    lines = source.splitlines()

    trimmed_lines = []
    skip_first_def = True
    for line in lines:
        if skip_first_def and line.strip().startswith("def "):
            skip_first_def = False
            continue
        trimmed_lines.append(line)

    filtered_lines = []
    in_docstring = False
    for line in trimmed_lines:
        stripped_line = line.strip()
        if '"""' in stripped_line:
            in_docstring = not in_docstring
            continue
        if in_docstring:
            continue
        if "log_function_definition(" in line or "stop_all_drivers()" in line or "clean_html(" in line:
            continue
        if stripped_line.startswith("return") or stripped_line.startswith("global"):
            continue
        filtered_lines.append(line)

    from inspect import signature
    sig = signature(fn)
    param_names = list(sig.parameters.keys())

    param_values = {}
    for name, val in zip(param_names, args):
        param_values[name] = val
    for k, v in kwargs.items():
        param_values[k] = v

    replaced_lines = []
    for line in filtered_lines:
        new_line = line
        for param, actual_val in param_values.items():
            pattern = rf"\b{param}={param}\b"
            repl = f'{param}={repr(actual_val)}'
            new_line = re.sub(pattern, repl, new_line)

        if "selenium_url" in new_line and kwargs.get('selenium_url') is not None:
            new_line = new_line.replace("selenium_url", repr(kwargs['selenium_url']))

        new_line = re.sub(r"driver\[_run_test_id\]", "driver", new_line)
        replaced_lines.append(new_line)

    dedented_code = textwrap.dedent("\n".join(replaced_lines)).rstrip() + "\n"

    directory = "tests"
    if not os.path.exists(directory):
        os.makedirs(directory)

    file_path = os.path.join(directory, f"function_calls{kwargs.get('_run_test_id', '1')}.py")
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(dedented_code)

###############################################################################
# Below are your selenium-related functions (fixed to consistently use global
# driver/test_variables and to accept/use the _run_test_id param)
###############################################################################
def create_driver(_run_test_id='1'):
    """
    Creates a driver and initializes test_variables for this run id.

    Usage:
        create_driver({})
    """
    global driver, test_variables
    test_variables[_run_test_id] = {}
    from selenium.webdriver.chrome.options import Options
    from testui.support.appium_driver import NewDriver
    options = Options()
    options.add_argument("disable-user-media-security")
    options.add_argument("disable-features=PasswordCheck,PasswordLeakDetection,SafetyCheck")
    options.add_argument("--window-size=1280,800")
    # options.page_load_strategy = 'eager'
    driver[_run_test_id] = (
        NewDriver()
        .set_logger()
        .set_browser('chrome')
    )
    if os.getenv("SELENIUM_URL"):
        driver[_run_test_id] = driver[_run_test_id].set_remote_url(os.getenv("SELENIUM_URL"))
    else:
        pass
        options.add_argument("--headless")
    streaming.stop_stream(_run_test_id)
    driver[_run_test_id] = driver[_run_test_id].set_selenium_driver(chrome_options=options)
    streaming.start_stream(driver[_run_test_id], run_id=_run_test_id, fps=5.0, jpeg_quality=70)
    driver[_run_test_id].navigate_to("https://google.com")
    log_function_definition(create_driver, _run_test_id=_run_test_id)
    return "driver created"


def _expand_charset(spec):
    """Return list of chars for a bracket charset like a-zA-Z0-9_."""
    chars = []
    i = 0
    L = len(spec)
    while i < L:
        if i + 2 < L and spec[i+1] == '-':
            start = spec[i]
            end = spec[i+2]
            chars.extend([chr(c) for c in range(ord(start), ord(end)+1)])
            i += 3
        else:
            chars.append(spec[i])
            i += 1
    return chars

def _generate_from_token(token, count):
    """Generate 'count' chars for a token."""
    if token == r'\d':
        pool = string.digits
    elif token == r'\w':
        pool = string.ascii_letters + string.digits + '_'
    elif token == '.':
        pool = string.ascii_letters + string.digits
    elif token.startswith('[') and token.endswith(']'):
        pool = ''.join(_expand_charset(token[1:-1]))
    else:
        pool = token
    return ''.join(random.choice(pool) for _ in range(count))

def _generate_from_regex(pattern):
    """
    Lightweight regex-to-string generator supporting \\d, \\w, ., [a-z], and {n} quantifier.
    """
    out = []
    token_re = re.compile(r'(\\d|\\w|\.|\[[^\]]+\]|.)' r'(?:\{(\d+)\})?')
    pos = 0
    for m in token_re.finditer(pattern):
        if m.start() != pos:
            gap = pattern[pos:m.start()]
            out.append(gap)
        token = m.group(1)
        count = int(m.group(2)) if m.group(2) else 1
        out.append(_generate_from_token(token, count))
        pos = m.end()
    if pos < len(pattern):
        out.append(pattern[pos:])
    return ''.join(out)


def set_variable(name, value='', _run_test_id='1', regex_const=''):
    """
    Set a variable for the running test.

    Usage:
        set_variable({'name': 'username', 'value': 'alice'})
        set_variable({'name': 'token', 'regex_const': '[A-Z]{3}\\d{4}'})

    - name: variable name (str)
    - value: explicit value; if provided, it's stored as-is
    - regex_const: optional simple regex-like construction to generate a random value. You can construct it like:
        supporting ONLY \\d, \\w, ., [a-z], and {n} quantifier.
    """
    global test_variables
    if _run_test_id not in test_variables:
        test_variables[_run_test_id] = {}

    if value != '':
        test_variables[_run_test_id][name] = value
        return test_variables[_run_test_id][name]

    if regex_const:
        generated = _generate_from_regex(regex_const)
        test_variables[_run_test_id][name] = generated
        return generated

    test_variables[_run_test_id][name] = ''
    return ''


def stop_driver(_run_test_id='1'):
    """
    Stops driver for given run id.

    Usage:
        stop_driver({})
    """
    global driver
    if _run_test_id in driver:
        try:
            streaming.stop_stream(_run_test_id)
        except Exception:
            print("Failed to stop stream cleanly")
        driver[_run_test_id].quit()
        log_function_definition(stop_driver, _run_test_id=_run_test_id)
        return "success"
    return "no driver"


def maximize_window(_run_test_id='1'):
    """
    Maximizes the window for given run id.

    Usage:
        maximize_window({})
    """
    global driver
    driver[_run_test_id].get_driver().maximize_window()
    log_function_definition(maximize_window, _run_test_id=_run_test_id)
    return "success maximizing"


def add_cookie(name, value, _run_test_id='1', use_vars='false'):
    """
    Adds cookie by name and value.

    Usage without variables:
        add_cookie({'name': 'sessionid', 'value': 'abc123'})

    Usage with variables:
        set_variable({'name': 'cookie_name', 'value': 'sessionid'})
        set_variable({'name': 'cookie_value', 'value': 'abc123'})
        add_cookie({'name': 'cookie_name', 'value': 'cookie_value', 'use_vars': 'true'})
    """
    global driver, test_variables
    if use_vars == 'true' and _run_test_id in test_variables:
        name = test_variables[_run_test_id].get(name, name)
        value = test_variables[_run_test_id].get(value, value)
    driver[_run_test_id].get_driver().add_cookie({'name': name, 'value': value})
    log_function_definition(add_cookie, name, value, _run_test_id=_run_test_id)
    return "Cookies added"


def navigate_to_url(url: str, _run_test_id='1', use_vars='false') -> str:
    """
    Navigates the browser to a specific URL.

    Usage without variables:
        navigate_to_url({'url': 'https://example.com'})

    Usage with variables:
        set_variable({'name': 'home', 'value': 'https://example.com'})
        navigate_to_url({'url': 'home', 'use_vars': True})
    """
    global driver, test_variables
    if use_vars == 'true' and _run_test_id in test_variables:
        url = test_variables[_run_test_id].get(url, url)
    driver[_run_test_id].navigate_to(url=url)
    log_function_definition(navigate_to_url, url, _run_test_id=_run_test_id)
    try:
        memories = _get_memories_for_url(url)
        if memories:
            mem_text = "\n".join(f"- [{m['category']}] {m['observation']}" for m in memories)
            return f"{url}\n\nPAGE MEMORIES ({len(memories)}):\n{mem_text}"
    except Exception:
        pass
    return url


def save_page_memory(url: str, observation: str, category: str = 'general', _run_test_id='1') -> str:
    """
    Saves an observation/learning about a specific page for future runs.
    Use this when you discover something non-obvious about how a page works:
    element types that differ from expectations, timing requirements, workarounds, etc.

    Categories: locator_hint, element_type, flow_behavior, timing, workaround, general

    Usage:
        save_page_memory({
            'url': 'https://app.example.com/signup',
            'observation': 'Country field is div[role=combobox], use select_option_from_dropdown',
            'category': 'element_type'
        })
    """
    domain, path = _parse_url_parts(url)
    data = _load_domain_memory(domain)
    if path not in data["pages"]:
        data["pages"][path] = {"memories": []}
    for mem in data["pages"][path]["memories"]:
        if mem["observation"] == observation and mem["category"] == category:
            return f"Memory already exists: {mem['id']}"
    now = datetime.now(timezone.utc).isoformat()
    memory_id = _generate_memory_id()
    data["pages"][path]["memories"].append({
        "id": memory_id,
        "observation": observation,
        "category": category,
        "created_at": now,
        "updated_at": now
    })
    _save_domain_memory(domain, data)
    return f"Memory saved: {memory_id}"


def get_page_memories(url: str, _run_test_id='1') -> str:
    """
    Retrieves all saved memories/observations for a specific page URL.
    Returns memories for the exact path AND domain-wide memories (path="/").
    Note: navigate_to_url already auto-loads memories, so use this only when
    you need to check memories without navigating.

    Usage:
        get_page_memories({'url': 'https://app.example.com/signup'})
    """
    memories = _get_memories_for_url(url)
    if not memories:
        return json.dumps({"url": url, "memories": [], "message": "No memories found for this page."})
    return json.dumps({"url": url, "memory_count": len(memories), "memories": memories})


def update_page_memory(memory_id: str, observation: str = '', delete: str = 'false', _run_test_id='1') -> str:
    """
    Updates or deletes an existing page memory by its ID.
    Use this when you discover a previous observation is outdated or wrong.

    To update: provide the memory_id and new observation text.
    To delete: provide the memory_id and set delete='true'.

    Usage (update):
        update_page_memory({'memory_id': 'm_a1b2c3', 'observation': 'New correct observation'})

    Usage (delete):
        update_page_memory({'memory_id': 'm_a1b2c3', 'delete': 'true'})
    """
    if not os.path.exists(MEMORY_DIR):
        return f"Memory {memory_id} not found."
    for filename in os.listdir(MEMORY_DIR):
        if not filename.endswith(".json"):
            continue
        domain = filename[:-5]
        data = _load_domain_memory(domain)
        for path_key, page_data in data.get("pages", {}).items():
            for i, mem in enumerate(page_data.get("memories", [])):
                if mem["id"] == memory_id:
                    if delete == 'true':
                        page_data["memories"].pop(i)
                        _save_domain_memory(domain, data)
                        return f"Memory {memory_id} deleted."
                    else:
                        mem["observation"] = observation
                        mem["updated_at"] = datetime.now(timezone.utc).isoformat()
                        _save_domain_memory(domain, data)
                        return f"Memory {memory_id} updated."
    return f"Memory {memory_id} not found."


def send_keys(locator_type: str, locator: str, value: str, _run_test_id='1', use_vars: str = 'false') -> str:
    """
    Types `value` into element specified by locator.

    Usage without variables:
        send_keys({'locator_type': 'css', 'locator': '#username', 'value': 'alice'})

    Usage with variables:
        set_variable({'name': 'u', 'value': 'alice'})
        send_keys({'locator_type': 'css', 'locator': '#username', 'value': 'u', 'use_vars': 'true'})
    """
    global driver, test_variables

    if use_vars == 'true' and _run_test_id in test_variables:
        value = test_variables[_run_test_id].get(value, value)

    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=10)
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).send_keys(value=value)
    log_function_definition(send_keys, locator_type, locator, value, _run_test_id=_run_test_id)
    return "sent keys"


def exists(locator_type: str, locator: str, _run_test_id='1') -> str:
    """
    Waits until element is visible (exists).

    Usage:
        exists({'locator_type': 'css', 'locator': '.nav'})
    """
    global driver
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=10)
    log_function_definition(exists, locator_type, locator, _run_test_id=_run_test_id)
    return "exists"

def exists_with_text(text: str, _run_test_id='1', use_vars: str = 'false') -> str:
    """
    Asserts that an element containing the given text exists (uses XPath).
    Usage (substring match):
        exists_with_text({'text': 'Welcome'})

    Usage with variables:
        set_variable({'name': 'u', 'value': 'alice'})
        exists_with_text({'text': 'u', 'use_vars': 'true'})
    """
    global driver
    if use_vars == 'true' and _run_test_id in test_variables:
        text = test_variables[_run_test_id].get(text, text)
    locator = f"//*[contains(text(), '{text}')]"
    driver[_run_test_id].e(locator_type='xpath', locator=locator).wait_until_exists(seconds=10)
    log_function_definition(exists_with_text, text, _run_test_id=_run_test_id)
    return "exists (text)"


def does_not_exist(locator_type: str, locator: str, _run_test_id='1') -> str:
    """
    Waits until element does NOT exist.

    Usage:
        does_not_exist({'locator_type': 'xpath', 'locator': '//div[@id=\"loading\"]'})
    """
    global driver
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).no().wait_until_exists(seconds=10)
    log_function_definition(does_not_exist, locator_type, locator, _run_test_id=_run_test_id)
    return "doesn't exists"


def scroll_to_element(locator_type: str, locator: str, _run_test_id='1') -> str:
    """
    Scrolls until the element is visible in the viewport.

    Usage:
        scroll_to_element({'locator_type': 'css', 'locator': '#footer'})
    """
    global driver
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=10)
    element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
    current_driver_instance = driver[_run_test_id].get_driver()
    current_driver_instance.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});", element)
    log_function_definition(scroll_to_element, locator_type, locator, _run_test_id=_run_test_id)
    return "scrolled"


def click(locator_type: str, locator: str, _run_test_id='1') -> str:
    """
    Clicks in the element defined by its `locator_type` (id, css, xpath)
    and its locator path associated.

    It will return a string saying clicked successfully on the element
    """
    global driver
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=10)
    from selenium.webdriver.common.action_chains import ActionChains
    element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
    actions = ActionChains(driver[_run_test_id])
    actions.move_to_element(element).perform()
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).click()
    log_function_definition(click, locator_type, locator)
    return "clicked successfully on the element"


def double_click(locator_type: str, locator: str, _run_test_id='1') -> str:
    """
    Double clicks on element.

    Usage:
        double_click({'locator_type': 'xpath', 'locator': '//button[@id=\"save\"]'})
    """
    global driver
    from selenium.webdriver.common.action_chains import ActionChains
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=10)
    element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
    actions = ActionChains(driver[_run_test_id].get_driver())
    actions.double_click(element).perform()
    log_function_definition(double_click, locator_type, locator, _run_test_id=_run_test_id)
    return "double clicked"


def right_click(locator_type: str, locator: str, _run_test_id='1') -> str:
    """
    Right clicks on element.

    Usage:
        right_click({'locator_type': 'css', 'locator': '.item .menu'})
    """
    global driver
    from selenium.webdriver.common.action_chains import ActionChains
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=10)
    element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
    actions = ActionChains(driver[_run_test_id].get_driver())
    actions.context_click(element).perform()
    log_function_definition(right_click, locator_type, locator, _run_test_id=_run_test_id)
    return "right clicked"


def hover(locator_type: str, locator: str, _run_test_id='1') -> str:
    """
    Hovers over an element (moves mouse without clicking). Useful for triggering
    dropdown menus, tooltips, or hover states.

    Usage:
        hover({'locator_type': 'css', 'locator': '.nav-menu'})
    """
    global driver
    from selenium.webdriver.common.action_chains import ActionChains
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=10)
    element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
    actions = ActionChains(driver[_run_test_id].get_driver())
    actions.move_to_element(element).perform()
    log_function_definition(hover, locator_type, locator, _run_test_id=_run_test_id)
    return "hovered"


def clear_and_type(locator_type: str, locator: str, value: str, _run_test_id='1', use_vars: str = 'false') -> str:
    """
    Clears an input field completely, then types the new value. Use this instead of
    send_keys when the field already has text that needs to be replaced.

    Usage without variables:
        clear_and_type({'locator_type': 'css', 'locator': '#email', 'value': 'new@email.com'})

    Usage with variables:
        set_variable({'name': 'email', 'value': 'new@email.com'})
        clear_and_type({'locator_type': 'css', 'locator': '#email', 'value': 'email', 'use_vars': 'true'})
    """
    global driver, test_variables
    if use_vars == 'true' and _run_test_id in test_variables:
        value = test_variables[_run_test_id].get(value, value)
    from selenium.webdriver.common.keys import Keys
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=10)
    element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
    element.click()
    element.send_keys(Keys.CONTROL + "a")
    element.send_keys(Keys.DELETE)
    element.clear()
    element.send_keys(value)
    log_function_definition(clear_and_type, locator_type, locator, value, _run_test_id=_run_test_id)
    return "cleared and typed"


def press_key(key: str, locator_type: str = '', locator: str = '', _run_test_id='1') -> str:
    """
    Presses a keyboard key. If locator is provided, presses the key on that element.
    Otherwise presses the key on the currently focused element.

    Supported keys: ENTER, TAB, ESCAPE, BACKSPACE, DELETE, SPACE,
    ARROW_UP, ARROW_DOWN, ARROW_LEFT, ARROW_RIGHT, HOME, END, PAGE_UP, PAGE_DOWN,
    F1-F12, SHIFT, CONTROL, ALT

    Usage (on focused element):
        press_key({'key': 'ENTER'})

    Usage (on specific element):
        press_key({'key': 'ESCAPE', 'locator_type': 'css', 'locator': '.modal'})
    """
    global driver
    from selenium.webdriver.common.keys import Keys
    key_map = {
        'ENTER': Keys.ENTER, 'RETURN': Keys.RETURN, 'TAB': Keys.TAB,
        'ESCAPE': Keys.ESCAPE, 'BACKSPACE': Keys.BACKSPACE, 'DELETE': Keys.DELETE,
        'SPACE': Keys.SPACE, 'ARROW_UP': Keys.ARROW_UP, 'ARROW_DOWN': Keys.ARROW_DOWN,
        'ARROW_LEFT': Keys.ARROW_LEFT, 'ARROW_RIGHT': Keys.ARROW_RIGHT,
        'HOME': Keys.HOME, 'END': Keys.END, 'PAGE_UP': Keys.PAGE_UP,
        'PAGE_DOWN': Keys.PAGE_DOWN, 'SHIFT': Keys.SHIFT, 'CONTROL': Keys.CONTROL,
        'ALT': Keys.ALT, 'F1': Keys.F1, 'F2': Keys.F2, 'F3': Keys.F3,
        'F4': Keys.F4, 'F5': Keys.F5, 'F6': Keys.F6, 'F7': Keys.F7,
        'F8': Keys.F8, 'F9': Keys.F9, 'F10': Keys.F10, 'F11': Keys.F11, 'F12': Keys.F12,
    }
    selenium_key = key_map.get(key.upper(), key)
    from selenium.webdriver.common.action_chains import ActionChains
    if locator_type and locator:
        driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=10)
        element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
        element.send_keys(selenium_key)
    else:
        actions = ActionChains(driver[_run_test_id].get_driver())
        actions.send_keys(selenium_key).perform()
    log_function_definition(press_key, key, locator_type, locator, _run_test_id=_run_test_id)
    return f"pressed {key}"


def select_option(locator_type: str, locator: str, option_text: str = '', option_value: str = '', _run_test_id='1') -> str:
    """
    Selects an option from a native <select> element by visible text or by value.
    Use option_text for human-readable selection, or option_value for value-based selection.

    Usage by text:
        select_option({'locator_type': 'css', 'locator': '#country', 'option_text': 'Spain'})

    Usage by value:
        select_option({'locator_type': 'id', 'locator': 'country', 'option_value': 'ES'})
    """
    global driver
    from selenium.webdriver.support.ui import Select
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=10)
    element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
    select = Select(element)
    if option_text:
        select.select_by_visible_text(option_text)
    elif option_value:
        select.select_by_value(option_value)
    log_function_definition(select_option, locator_type, locator, option_text, option_value, _run_test_id=_run_test_id)
    return f"selected option"


def select_option_from_dropdown(
    trigger_locator_type: str, trigger_locator: str,
    option_text: str, _run_test_id='1'
) -> str:
    """
    Handles custom (non-native) dropdown/combobox components. Clicks the trigger element
    to open the dropdown, then finds and clicks the option matching the given text.
    Works with divs styled as selects, role="listbox", role="combobox", etc.

    Usage:
        select_option_from_dropdown({
            'trigger_locator_type': 'css', 'trigger_locator': '.custom-select',
            'option_text': 'Spain'
        })
    """
    global driver
    import time
    driver[_run_test_id].e(locator_type=trigger_locator_type, locator=trigger_locator).wait_until_exists(seconds=10)
    driver[_run_test_id].e(locator_type=trigger_locator_type, locator=trigger_locator).click()
    time.sleep(0.5)
    option_xpath = (
        f"//*[self::li or self::div or self::span or self::option or @role='option']"
        f"[normalize-space(.)='{option_text}' or contains(normalize-space(.),'{option_text}')]"
    )
    driver[_run_test_id].e(locator_type='xpath', locator=option_xpath).wait_until_exists(seconds=5)
    driver[_run_test_id].e(locator_type='xpath', locator=option_xpath).click()
    log_function_definition(
        select_option_from_dropdown, trigger_locator_type, trigger_locator,
        option_text, _run_test_id=_run_test_id
    )
    return f"selected '{option_text}' from dropdown"


def get_text(locator_type: str, locator: str, _run_test_id='1') -> str:
    """
    Returns the visible text content of an element. Useful for reading values,
    labels, messages, or any displayed text.

    Usage:
        get_text({'locator_type': 'css', 'locator': '.alert-message'})
    """
    global driver
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=5)
    element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
    text = element.text
    log_function_definition(get_text, locator_type, locator, _run_test_id=_run_test_id)
    return text


def get_attribute(locator_type: str, locator: str, attribute: str, _run_test_id='1') -> str:
    """
    Returns the value of a specific attribute of an element. Useful for reading
    href, src, data-* attributes, value, etc.

    Usage:
        get_attribute({'locator_type': 'css', 'locator': 'a.download', 'attribute': 'href'})
        get_attribute({'locator_type': 'id', 'locator': 'email', 'attribute': 'value'})
    """
    global driver
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=5)
    element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
    value = element.get_attribute(attribute)
    log_function_definition(get_attribute, locator_type, locator, attribute, _run_test_id=_run_test_id)
    return value or ""


def upload_file(locator_type: str, locator: str, file_path: str, _run_test_id='1') -> str:
    """
    Uploads a file by sending the file path to a file input element.
    The locator should point to an <input type="file"> element.

    Usage:
        upload_file({'locator_type': 'css', 'locator': 'input[type="file"]', 'file_path': '/path/to/file.pdf'})
    """
    global driver
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=10)
    element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
    element.send_keys(file_path)
    log_function_definition(upload_file, locator_type, locator, file_path, _run_test_id=_run_test_id)
    return "file uploaded"


def drag_and_drop(
    source_locator_type: str, source_locator: str,
    target_locator_type: str, target_locator: str,
    _run_test_id='1'
) -> str:
    """
    Drags an element from source and drops it on target.

    Usage:
        drag_and_drop({
            'source_locator_type': 'css', 'source_locator': '.draggable-item',
            'target_locator_type': 'css', 'target_locator': '.drop-zone'
        })
    """
    global driver
    from selenium.webdriver.common.action_chains import ActionChains
    driver[_run_test_id].e(locator_type=source_locator_type, locator=source_locator).wait_until_exists(seconds=10)
    driver[_run_test_id].e(locator_type=target_locator_type, locator=target_locator).wait_until_exists(seconds=10)
    source = driver[_run_test_id].e(locator_type=source_locator_type, locator=source_locator).get_element()
    target = driver[_run_test_id].e(locator_type=target_locator_type, locator=target_locator).get_element()
    actions = ActionChains(driver[_run_test_id].get_driver())
    actions.drag_and_drop(source, target).perform()
    log_function_definition(
        drag_and_drop, source_locator_type, source_locator,
        target_locator_type, target_locator, _run_test_id=_run_test_id
    )
    return "dragged and dropped"


def wait_for_url_contains(text: str, timeout: int = 10, _run_test_id='1') -> str:
    """
    Waits until the current URL contains the specified text. Useful for waiting
    after navigation, redirects, or form submissions.

    Usage:
        wait_for_url_contains({'text': '/dashboard'})
        wait_for_url_contains({'text': 'success', 'timeout': 15})
    """
    global driver
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    WebDriverWait(driver[_run_test_id].get_driver(), timeout).until(EC.url_contains(text))
    log_function_definition(wait_for_url_contains, text, timeout, _run_test_id=_run_test_id)
    return f"url now contains '{text}'"


def execute_js(script: str, _run_test_id='1') -> str:
    """
    Executes arbitrary JavaScript in the browser and returns the result.
    Useful for complex interactions that cannot be done with standard Selenium methods,
    like scrolling to specific coordinates, reading computed styles, or triggering custom events.

    Usage:
        execute_js({'script': 'window.scrollTo(0, document.body.scrollHeight)'})
        execute_js({'script': 'return document.title'})
    """
    global driver
    result = driver[_run_test_id].get_driver().execute_script(script)
    log_function_definition(execute_js, script, _run_test_id=_run_test_id)
    return str(result) if result is not None else "executed"


def switch_to_alert(action: str = 'accept', text: str = '', _run_test_id='1') -> str:
    """
    Handles browser alert/confirm/prompt dialogs.

    - action='accept': Clicks OK/Accept
    - action='dismiss': Clicks Cancel/Dismiss
    - action='get_text': Returns the alert text without closing it
    - text: For prompt dialogs, types this text before accepting

    Usage:
        switch_to_alert({'action': 'accept'})
        switch_to_alert({'action': 'dismiss'})
        switch_to_alert({'action': 'get_text'})
        switch_to_alert({'action': 'accept', 'text': 'my input'})
    """
    global driver
    import time
    time.sleep(0.5)
    alert = driver[_run_test_id].get_driver().switch_to.alert
    if action == 'get_text':
        alert_text = alert.text
        log_function_definition(switch_to_alert, action, text, _run_test_id=_run_test_id)
        return alert_text
    if text:
        alert.send_keys(text)
    if action == 'accept':
        alert.accept()
    elif action == 'dismiss':
        alert.dismiss()
    log_function_definition(switch_to_alert, action, text, _run_test_id=_run_test_id)
    return f"alert {action}ed"


def go_back(_run_test_id='1') -> str:
    """
    Navigates the browser back (like clicking the back button).

    Usage:
        go_back({})
    """
    global driver
    driver[_run_test_id].get_driver().back()
    log_function_definition(go_back, _run_test_id=_run_test_id)
    return "navigated back"


def go_forward(_run_test_id='1') -> str:
    """
    Navigates the browser forward (like clicking the forward button).

    Usage:
        go_forward({})
    """
    global driver
    driver[_run_test_id].get_driver().forward()
    log_function_definition(go_forward, _run_test_id=_run_test_id)
    return "navigated forward"


def refresh_page(_run_test_id='1') -> str:
    """
    Refreshes the current page.

    Usage:
        refresh_page({})
    """
    global driver
    driver[_run_test_id].get_driver().refresh()
    log_function_definition(refresh_page, _run_test_id=_run_test_id)
    return "page refreshed"


_INTERACTIVE_ELEMENTS_JS = """
return (function() {
    const INTERACTIVE = 'a[href],button,input,select,textarea,details,summary,'
        + '[role],[onclick],[tabindex]:not([tabindex="-1"]),[contenteditable="true"]';

    function bestSelector(el) {
        if (el.id) return {type: 'id', selector: el.id};
        if (el.name) return {type: 'css', selector: el.tagName.toLowerCase() + '[name="' + el.name + '"]'};
        if (el.getAttribute('data-testid'))
            return {type: 'css', selector: '[data-testid="' + el.getAttribute('data-testid') + '"]'};
        if (el.getAttribute('aria-label'))
            return {type: 'css', selector: el.tagName.toLowerCase() + '[aria-label="' + el.getAttribute('aria-label') + '"]'};
        var cls = Array.from(el.classList).filter(function(c) { return !/^[a-z]{1,2}-/.test(c) && c.length < 40; });
        if (cls.length > 0)
            return {type: 'css', selector: el.tagName.toLowerCase() + '.' + cls.slice(0, 2).join('.')};
        return {type: 'css', selector: el.tagName.toLowerCase()};
    }

    function getLabel(el) {
        if (el.labels && el.labels.length > 0) return el.labels[0].innerText.trim().substring(0, 80);
        if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
        if (el.getAttribute('aria-labelledby')) {
            var lbl = document.getElementById(el.getAttribute('aria-labelledby'));
            if (lbl) return lbl.innerText.trim().substring(0, 80);
        }
        if (el.placeholder) return el.placeholder;
        var prev = el.previousElementSibling;
        if (prev && prev.tagName === 'LABEL') return prev.innerText.trim().substring(0, 80);
        var parent = el.closest('[class*="field"],[class*="form-group"],[class*="input"]');
        if (parent) {
            var lbl2 = parent.querySelector('label,legend,.label,[class*="label"]');
            if (lbl2 && lbl2 !== el) return lbl2.innerText.trim().substring(0, 80);
        }
        return null;
    }

    function getSelectOptions(el) {
        if (el.tagName === 'SELECT') {
            return Array.from(el.options).slice(0, 15).map(function(o) {
                return {value: o.value, text: o.text.trim()};
            });
        }
        if (el.getAttribute('role') === 'listbox' || el.getAttribute('role') === 'combobox') {
            var opts = el.querySelectorAll('[role="option"]');
            return Array.from(opts).slice(0, 15).map(function(o) {
                return {value: o.getAttribute('data-value') || '', text: o.innerText.trim()};
            });
        }
        return null;
    }

    var elements = document.querySelectorAll(INTERACTIVE);
    var results = [];
    for (var i = 0; i < elements.length; i++) {
        var el = elements[i];
        var rect = el.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) continue;
        var style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;

        var sel = bestSelector(el);
        var entry = {
            index: results.length,
            tag: el.tagName.toLowerCase(),
            type: el.type || null,
            role: el.getAttribute('role') || null,
            id: el.id || null,
            name: el.name || null,
            text: (el.innerText || '').trim().substring(0, 80) || null,
            label: getLabel(el),
            placeholder: el.placeholder || null,
            href: el.href || null,
            value: el.value || null,
            checked: typeof el.checked === 'boolean' ? el.checked : null,
            disabled: el.disabled || null,
            aria_expanded: el.getAttribute('aria-expanded'),
            aria_haspopup: el.getAttribute('aria-haspopup'),
            aria_selected: el.getAttribute('aria-selected'),
            best_locator: sel,
            options: getSelectOptions(el)
        };

        // Remove null values to keep output compact
        var clean = {};
        for (var key in entry) {
            if (entry[key] !== null && entry[key] !== undefined && entry[key] !== false) {
                clean[key] = entry[key];
            }
        }
        results.push(clean);
    }

    return JSON.stringify({
        url: window.location.href,
        title: document.title,
        element_count: results.length,
        elements: results
    });
})();
"""


def get_interactive_elements(_run_test_id='1') -> str:
    """
    Returns a structured JSON summary of all interactive elements on the page.
    Each element includes its tag, role, ARIA attributes, label, and best locator.
    This is much more compact and accurate than raw HTML for deciding what to interact with.
    Also returns any saved page memories for the current URL.

    Usage:
        get_interactive_elements({})
    """
    global driver
    result = driver[_run_test_id].get_driver().execute_script(_INTERACTIVE_ELEMENTS_JS)
    log_function_definition(get_interactive_elements, _run_test_id=_run_test_id)
    try:
        current_url = driver[_run_test_id].get_driver().current_url
        memories = _get_memories_for_url(current_url)
        if memories:
            mem_text = "\n".join(f"- [{m['id']}][{m['category']}] {m['observation']}" for m in memories)
            result += f"\n\nPAGE MEMORIES ({len(memories)}):\n{mem_text}"
    except Exception:
        pass
    return result


def get_page_html(_run_test_id='1') -> str:
    """
    Returns the full cleaned page HTML. Use this only as a fallback when
    get_interactive_elements does not provide enough context (e.g., you need
    to understand page layout, read static text content, or find non-interactive elements).

    Usage:
        get_page_html({})
    """
    global driver
    content = driver[_run_test_id].get_driver().page_source
    html_content = clean_html(content)
    log_function_definition(get_page_html, _run_test_id=_run_test_id)
    return html_content


def return_current_url(_run_test_id='1') -> str:
    """
    Returns current URL.

    Usage:
        return_current_url({})
    """
    global driver
    url = driver[_run_test_id].get_driver().current_url
    log_function_definition(return_current_url, _run_test_id=_run_test_id)
    return url


def change_windows_tabs(_run_test_id='1') -> str:
    """
    Switches to another window/tab and returns cleaned HTML of the new active page.

    Usage:
        change_windows_tabs({})
    """
    global driver
    selenium_driver = driver[_run_test_id].get_driver()
    print("Parent window title: " + selenium_driver.title)
    p = selenium_driver.current_window_handle
    chwd = selenium_driver.window_handles
    for w in chwd:
        if w != p:
            selenium_driver.switch_to.window(w)
            break
    print("frame changed: " + selenium_driver.title)
    log_function_definition(change_windows_tabs, _run_test_id=_run_test_id)
    content = driver[_run_test_id].get_driver().page_source
    html_content = clean_html(content)
    return html_content


def change_frame_by_id(frame_name, _run_test_id='1') -> str:
    """
    Switches focus to the specified frame or iframe, by index or name.

    Usage:
        change_frame_by_id({'frame_name': 'frame-name'})
        change_frame_by_id({'frame_name': 1})
    """
    global driver
    driver[_run_test_id].get_driver().switch_to.frame(frame_name)
    log_function_definition(change_frame_by_id, frame_name, _run_test_id=_run_test_id)
    return "frame_changed"


def change_frame_by_locator(locator_type: str = None, locator: str = None, _run_test_id='1') -> str:
    """
    Switches focus to the specified iframe by locator.

    Usage:
        change_frame_by_locator({'locator_type': 'css', 'locator': 'iframe#player'})
    """
    global driver
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=10)
    element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
    driver[_run_test_id].get_driver().switch_to.frame(element)
    log_function_definition(change_frame_by_locator, locator_type, locator, _run_test_id=_run_test_id)
    return "frame_changed"


def change_frame_to_original(_run_test_id='1') -> str:
    """
    Switches focus back to the main document.

    Usage:
        change_frame_to_original({})
    """
    global driver
    driver[_run_test_id].get_driver().switch_to.default_content()
    log_function_definition(change_frame_to_original, _run_test_id=_run_test_id)
    return "frame_changed"

