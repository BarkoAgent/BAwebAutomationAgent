import inspect
import re
import textwrap
import os
import sys
import json
import time
import random
import string
import logging
import functools
import sys
import ba_ws_sdk.streaming as streaming
import ba_ws_sdk.file_system as file_system
from testui.support.testui_driver import TestUIDriver

DEFAULT_TIMEOUT = int(os.getenv("DEFAULT_TIMEOUT", "10"))

test_variables = {}
driver: dict[str, TestUIDriver] = {}
run_test_id = ""

# Files to ignore when scanning the attachments directory
_IGNORED_FILES = {'.DS_Store', 'Thumbs.db', 'desktop.ini'}


_ANSI_ESCAPE_RE = re.compile(r'\x1b\[[0-9;]*m')

def _sanitize_error_message(raw: str) -> str:
    """Extract the human-readable part of a testui exception, stripping tracebacks.

    testui appends traceback lines by direct string concatenation (with ANSI color
    codes as delimiters), so a traceback may appear mid-line rather than on its own
    line. We strip ANSI codes first, then cut at the first File "..." occurrence.
    """
    if not raw or not raw.strip():
        return raw

    # Remove ANSI escape codes so traceback markers are not hidden inside them
    clean = _ANSI_ESCAPE_RE.sub('', raw)

    _SKIP_LINES = {
        'There were errors during the UI testing, check the logs:',
        'There were errors during the UI testing, check the logs',
    }

    meaningful = []
    for line in clean.splitlines():
        stripped = line.strip()

        # Skip known boilerplate header lines
        if stripped in _SKIP_LINES:
            continue

        # Stop at a standalone Traceback header
        if stripped.startswith('Traceback (most recent call last):'):
            break

        # Detect File "..." pattern — may be at start (pure traceback line)
        # or embedded mid-line (traceback concatenated to the message)
        file_idx = line.find('  File "')
        if file_idx == 0:
            # This line is entirely a traceback entry — stop
            break
        elif file_idx > 0:
            # Traceback was concatenated onto the end of the message line
            prefix = line[:file_idx].strip()
            # Strip leading device-name prefix (e.g. "Device: ", "Chrome: ")
            if ': ' in prefix:
                prefix = prefix[prefix.index(': ') + 2:]
            if prefix:
                meaningful.append(prefix)
            break

        # Skip leading empty lines
        if not stripped and not meaningful:
            continue

        # Strip device-name prefix (e.g. "Device: Element was not found...")
        if ': ' in stripped:
            candidate = stripped[:stripped.index(': ')]
            if ' ' not in candidate:  # device names have no spaces
                stripped = stripped[len(candidate) + 2:]

        if stripped:
            meaningful.append(stripped)

    return ' '.join(meaningful) if meaningful else raw


def _error_sanitizer(fn):
    """Wrap fn so any exception is re-raised as RuntimeError with a clean message."""
    @functools.wraps(fn)
    def _wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            raise RuntimeError(_sanitize_error_message(str(e))) from None
    return _wrapper


def _is_valid_download(name: str) -> bool:
    """Return True if *name* looks like a real user file, not a system/temp artifact."""
    if name in _IGNORED_FILES or name.startswith('.'):
        return False
    if name.endswith('.crdownload') or name.endswith('.tmp') or name.endswith('.part'):
        return False
    return True


def clean_html(html_content):
    for tag in ['script', 'style', 'svg']:
        html_content = re.sub(rf'<{tag}[^>]*>.*?</{tag}>', '', html_content, flags=re.DOTALL)
    return html_content

def stop_all_drivers(**kwargs):
    import subprocess
    global driver

    for run_id, drv in list(driver.items()):
        try:
            streaming.stop_stream(run_id)
        except Exception:
            pass
        try:
            file_system.clear_downloads(run_id)
        except Exception:
            pass

    if sys.platform == 'win32':
        # On Windows, collect all chromedriver PIDs and kill their entire
        # process trees in one shot — no waiting, no per-driver timeouts.
        pids = []
        for run_id, drv in list(driver.items()):
            try:
                proc = drv.get_driver().service.process
                if proc and proc.pid:
                    pids.append(str(proc.pid))
            except Exception:
                pass
        if pids:
            try:
                args = ['taskkill', '/F', '/T']
                for pid in pids:
                    args += ['/PID', pid]
                subprocess.run(args, capture_output=True)
                print(f"Force-killed {len(pids)} chromedriver process tree(s).")
            except Exception as e:
                print(f"⚠️ taskkill failed: {e}")
    else:
        import threading
        for run_id, drv in list(driver.items()):
            try:
                t = threading.Thread(target=drv.quit, daemon=True)
                t.start()
                t.join(timeout=3)
                if t.is_alive():
                    print(f"⚠️ Driver '{run_id}' quit timed out, skipping.")
                else:
                    print(f"✅ Driver '{run_id}' stopped.")
            except Exception as e:
                print(f"⚠️ Error stopping driver '{run_id}': {e}")

    driver.clear()
    print("All drivers stopped and entries cleared.")

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

        # Second pass: replace remaining bare variable references (needed for assertion logic
        # where values appear outside of keyword-arg calls, e.g. `if actual != expected_text:`)
        for param, actual_val in param_values.items():
            new_line = re.sub(rf'\b{re.escape(param)}\b', repr(actual_val), new_line)

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
    options.page_load_strategy = 'eager'
    download_dir = str(file_system.get_attachments_dir())
    os.makedirs(download_dir, exist_ok=True)
    prefs = {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": False,
        "plugins.always_open_pdf_externally": True,
    }
    options.add_experimental_option("prefs", prefs)
    driver[_run_test_id] = (
        NewDriver()
        .set_logger()
        .set_browser('chrome')
    )
    if os.getenv("SELENIUM_URL"):
        driver[_run_test_id] = driver[_run_test_id].set_remote_url(os.getenv("SELENIUM_URL"))
    else:
        options.add_argument("--headless=new")
        options.add_argument("--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36")
    streaming.stop_stream(_run_test_id)
    driver[_run_test_id] = driver[_run_test_id].set_selenium_driver(chrome_options=options)
    # CDP command to enable downloads — required for headless Chrome
    # where Chrome prefs alone are ignored for download behavior
    try:
        driver[_run_test_id].get_driver().execute_cdp_cmd(
            "Page.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": download_dir},
        )
    except Exception as e:
        logging.warning(f"[Download] CDP setDownloadBehavior failed: {e}")
    streaming.start_stream(driver[_run_test_id], run_id=_run_test_id, fps=5.0, jpeg_quality=70)
    driver[_run_test_id].navigate_to("https://google.com")
    log_function_definition(create_driver, _run_test_id=_run_test_id)
    return "driver created"


def _xpath_escape(s: str) -> str:
    """Escape a string for safe use in XPath contains()."""
    if "'" not in s:
        return f"'{s}'"
    if '"' not in s:
        return f'"{s}"'
    parts = s.split("'")
    return "concat('" + "',\"'\",'".join(parts) + "')"


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
        try:
            file_system.clear_downloads(_run_test_id)
        except Exception:
            pass
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
    driver[_run_test_id].get_driver().set_window_size(1280, 800)
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
    return url


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
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=DEFAULT_TIMEOUT)
    log_function_definition(exists, locator_type, locator, _run_test_id=_run_test_id)
    return "exists"

def exists_with_text(text: str, scope_locator_type: str = '', scope_locator: str = '', _run_test_id='1', use_vars: str = 'false') -> str:
    """
    Asserts that an element containing the given text exists.
    Optionally scope the search to descendants of a container element so the match
    cannot accidentally come from an unrelated region of the page. The scope locator
    keeps its native type (css or xpath) — use whatever is stable for the page.

    Usage (substring match anywhere on page):
        exists_with_text({'text': 'Welcome'})

    Usage scoped to a container (recommended for "verify that <region> contains <text>"):
        exists_with_text({'text': 'Test t-shirt', 'scope_locator_type': 'css', 'scope_locator': '#cart_content'})
        exists_with_text({'text': 'Test t-shirt', 'scope_locator_type': 'xpath', 'scope_locator': "//*[@id='cart_content']"})

    Usage with variables:
        set_variable({'name': 'u', 'value': 'alice'})
        exists_with_text({'text': 'u', 'use_vars': 'true'})
    """
    global driver
    if use_vars == 'true' and _run_test_id in test_variables:
        text = test_variables[_run_test_id].get(text, text)

    if scope_locator:
        from selenium.webdriver.common.by import By
        driver[_run_test_id].e(locator_type=scope_locator_type, locator=scope_locator).wait_until_exists(seconds=DEFAULT_TIMEOUT)
        deadline = time.time() + DEFAULT_TIMEOUT
        last_err = None
        while time.time() < deadline:
            try:
                scope_el = driver[_run_test_id].e(locator_type=scope_locator_type, locator=scope_locator).get_element()
                if scope_el.find_elements(By.XPATH, f".//*[contains(text(), {_xpath_escape(text)})]"):
                    log_function_definition(exists_with_text, text, scope_locator_type=scope_locator_type, scope_locator=scope_locator, _run_test_id=_run_test_id)
                    return "exists (text, scoped)"
            except Exception as e:
                last_err = e
            time.sleep(0.5)
        raise AssertionError(
            f"exists_with_text failed: text '{text}' not found inside scope "
            f"({scope_locator_type}='{scope_locator}'){f' — last error: {last_err}' if last_err else ''}"
        )

    locator = f"//*[contains(text(), {_xpath_escape(text)})]"
    driver[_run_test_id].e(locator_type='xpath', locator=locator).wait_until_exists(seconds=DEFAULT_TIMEOUT)
    log_function_definition(exists_with_text, text, scope_locator_type=scope_locator_type, scope_locator=scope_locator, _run_test_id=_run_test_id)
    return "exists (text)"


def does_not_exist(locator_type: str, locator: str, _run_test_id='1') -> str:
    """
    Waits until element does NOT exist.

    Usage:
        does_not_exist({'locator_type': 'xpath', 'locator': '//div[@id=\"loading\"]'})
    """
    global driver
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).no().wait_until_exists(seconds=DEFAULT_TIMEOUT)
    log_function_definition(does_not_exist, locator_type, locator, _run_test_id=_run_test_id)
    return "doesn't exists"


def assert_text_equals(locator_type: str, locator: str, expected_text: str, scope_locator_type: str = '', scope_locator: str = '', _run_test_id='1') -> str:
    """
    Asserts that the visible text of an element exactly equals expected_text.
    Raises AssertionError with a clear message if the text does not match.

    Optionally scope the element lookup to descendants of a container element so
    the target is disambiguated when the same locator would match multiple regions
    (e.g., product grid vs. cart). The scope locator keeps its native type.

    Usage (unscoped):
        assert_text_equals({'locator_type': 'css', 'locator': 'h1', 'expected_text': 'Welcome'})

    Usage scoped to a container (recommended for "verify that <region> shows <text>"):
        assert_text_equals({'locator_type': 'css', 'locator': '.product-name',
                            'scope_locator_type': 'css', 'scope_locator': '#cart_content',
                            'expected_text': 'Test t-shirt (should be 15 in total)'})
    """
    global driver
    if scope_locator:
        from selenium.webdriver.common.by import By
        driver[_run_test_id].e(locator_type=scope_locator_type, locator=scope_locator).wait_until_exists(seconds=DEFAULT_TIMEOUT)
        scope_el = driver[_run_test_id].e(locator_type=scope_locator_type, locator=scope_locator).get_element()
        by_map = {'css': By.CSS_SELECTOR, 'xpath': By.XPATH, 'id': By.ID, 'name': By.NAME,
                  'class_name': By.CLASS_NAME, 'tag_name': By.TAG_NAME, 'link_text': By.LINK_TEXT,
                  'partial_link_text': By.PARTIAL_LINK_TEXT}
        by = by_map.get(locator_type, By.CSS_SELECTOR)
        inner_locator = locator
        if by == By.XPATH and not locator.startswith('.'):
            inner_locator = '.' + locator if locator.startswith('//') else './/' + locator.lstrip('/')
        deadline = time.time() + DEFAULT_TIMEOUT
        last_err = None
        element = None
        while time.time() < deadline:
            try:
                scope_el = driver[_run_test_id].e(locator_type=scope_locator_type, locator=scope_locator).get_element()
                matches = scope_el.find_elements(by, inner_locator)
                if matches:
                    element = matches[0]
                    break
            except Exception as e:
                last_err = e
            time.sleep(0.5)
        if element is None:
            raise AssertionError(
                f"assert_text_equals failed: locator '{locator}' ({locator_type}) not found inside scope "
                f"({scope_locator_type}='{scope_locator}'){f' — last error: {last_err}' if last_err else ''}"
            )
    else:
        driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=DEFAULT_TIMEOUT)
        element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
    actual_text = element.text
    if actual_text != expected_text:
        raise AssertionError(f"assert_text_equals failed: expected '{expected_text}', got '{actual_text}'")
    log_function_definition(assert_text_equals, locator_type, locator, expected_text,
                            scope_locator_type=scope_locator_type, scope_locator=scope_locator,
                            _run_test_id=_run_test_id)
    return f"text equals '{expected_text}'"


def assert_url_contains(expected_url: str, _run_test_id='1') -> str:
    """
    Asserts that the current browser URL contains expected_url as a substring.
    Raises AssertionError with a clear message if the URL does not match.

    Usage:
        assert_url_contains({'expected_url': '/dashboard'})
    """
    global driver
    current_url = driver[_run_test_id].get_driver().current_url
    if expected_url not in current_url:
        raise AssertionError(f"assert_url_contains failed: expected URL to contain '{expected_url}', got '{current_url}'")
    log_function_definition(assert_url_contains, expected_url, _run_test_id=_run_test_id)
    return f"url contains '{expected_url}'"


def scroll_to_element(locator_type: str, locator: str, _run_test_id='1') -> str:
    """
    Scrolls until the element is visible in the viewport.

    Usage:
        scroll_to_element({'locator_type': 'css', 'locator': '#footer'})
    """
    global driver
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=DEFAULT_TIMEOUT)
    element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
    current_driver_instance = driver[_run_test_id].get_driver()
    current_driver_instance.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});", element)
    log_function_definition(scroll_to_element, locator_type, locator, _run_test_id=_run_test_id)
    return "scrolled"


def click(locator_type: str, locator: str, _run_test_id='1') -> str:
    """
    Clicks the element identified by `locator_type` (id, css, xpath) and `locator`.

    Scrolls the element into view first, then performs a standard Selenium mouse click.
    This simulates a real user click including mouse movement, which is suitable for most
    interactive elements. If the element does not respond (e.g. React/Vue/Angular buttons
    with synthetic event handlers), use js_click instead.
    """
    global driver
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(DEFAULT_TIMEOUT)
    from selenium.webdriver.common.action_chains import ActionChains
    selenium_driver = driver[_run_test_id].get_driver()
    element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
    ActionChains(selenium_driver).move_to_element(element).perform()
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).click()
    log_function_definition(click, locator_type, locator, _run_test_id=_run_test_id)
    return "clicked successfully on the element"


def js_click(locator_type: str, locator: str, _run_test_id='1') -> str:
    """
    Clicks the element using a direct JavaScript DOM click, bypassing Selenium's mouse
    event simulation.

    Use this instead of click when:
    - The element is rendered by a JS framework (React, Vue, Angular) and does not respond
      to a standard Selenium click.
    - The element is partially covered by an overlay or sticky header that intercepts mouse
      events, but the element itself is present in the DOM.
    - click appears to succeed (no error) but the expected UI change does not happen.

    Note: js_click does NOT fire mousedown/mouseup/mouseover events — only the click event.
    If the app requires hover state or mousedown handlers to work (e.g. custom drag targets,
    canvas elements), use click instead.
    """
    global driver
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(DEFAULT_TIMEOUT)
    from selenium.webdriver.common.action_chains import ActionChains
    selenium_driver = driver[_run_test_id].get_driver()
    element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
    ActionChains(selenium_driver).move_to_element(element).perform()
    selenium_driver.execute_script("arguments[0].click();", element)
    log_function_definition(js_click, locator_type, locator, _run_test_id=_run_test_id)
    return "clicked successfully on the element using JavaScript"


def double_click(locator_type: str, locator: str, _run_test_id='1') -> str:
    """
    Double clicks on element.

    Usage:
        double_click({'locator_type': 'xpath', 'locator': '//button[@id=\"save\"]'})
    """
    global driver
    from selenium.webdriver.common.action_chains import ActionChains
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=DEFAULT_TIMEOUT)
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
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=DEFAULT_TIMEOUT)
    element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
    actions = ActionChains(driver[_run_test_id].get_driver())
    actions.context_click(element).perform()
    log_function_definition(right_click, locator_type, locator, _run_test_id=_run_test_id)
    return "right clicked"


def get_page_html(_run_test_id='1') -> str:
    """
    Returns cleaned page HTML.

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


# ─── File Download (browser-specific) ────────────────────────────────────────

def wait_for_download(timeout='30', _run_test_id='1') -> str:
    """
    Waits for a browser file download to complete. Call this AFTER clicking
    a download button or link. Returns the downloaded file name and size.

    Selenium does not have native download events, so this polls the
    attachments directory for new or recently modified files and
    .crdownload in-progress markers.

    Handles duplicates: if Chrome creates a file with a ' (N)' suffix,
    it is renamed to overwrite the original, keeping a single clean copy.

    Args:
        timeout: Maximum seconds to wait for the download to complete (default 30).

    Usage:
        wait_for_download({})
        wait_for_download({'timeout': '60'})
    """
    try:
        timeout_secs = float(timeout)
    except (TypeError, ValueError):
        timeout_secs = 30.0

    attachments_dir = file_system.get_attachments_dir()
    os.makedirs(str(attachments_dir), exist_ok=True)

    call_time = time.time()

    # Snapshot existing files with their mtimes
    existing_snapshot = {}
    if attachments_dir.is_dir():
        for f in attachments_dir.iterdir():
            if f.is_file() and _is_valid_download(f.name):
                try:
                    existing_snapshot[f.name] = f.stat().st_mtime
                except OSError:
                    pass

    deadline = call_time + timeout_secs
    new_file = None

    while time.time() < deadline:
        time.sleep(0.5)

        if not attachments_dir.is_dir():
            continue

        # Scan directory
        in_progress = []
        current_files = {}
        for f in attachments_dir.iterdir():
            if not f.is_file():
                continue
            if f.name.endswith('.crdownload'):
                in_progress.append(f.name)
                continue
            if not _is_valid_download(f.name):
                continue
            try:
                current_files[f.name] = f.stat().st_mtime
            except OSError:
                pass

        if in_progress:
            # A download is actively in progress — keep waiting
            continue

        # 1. Detect brand-new files (not in snapshot)
        new_names = set(current_files.keys()) - set(existing_snapshot.keys())
        if new_names:
            new_file = max(new_names, key=lambda n: current_files[n])
            break

        # 2. Detect files whose mtime changed since the snapshot
        #    (covers overwrite of same file, same size)
        for name, mtime in current_files.items():
            old_mtime = existing_snapshot.get(name)
            if old_mtime is not None and mtime > old_mtime:
                new_file = name
                break

        if new_file:
            break

    # Wait for file size to stabilize (fully written)
    if new_file:
        stable_size = -1
        for _ in range(6):  # up to 3 seconds
            try:
                current_size = (attachments_dir / new_file).stat().st_size
            except OSError:
                break
            if current_size == stable_size and current_size > 0:
                break
            stable_size = current_size
            time.sleep(0.5)

    if new_file is None:
        # Clean up any leftover .crdownload partial files
        if attachments_dir.is_dir():
            for f in attachments_dir.iterdir():
                if f.is_file() and f.name.endswith('.crdownload'):
                    try:
                        f.unlink()
                    except OSError:
                        pass
        return json.dumps({
            "status": "error",
            "error": f"No download detected after {timeout_secs}s. Make sure you clicked a download link or button before calling wait_for_download.",
        })

    file_path = str((attachments_dir / new_file).resolve())
    try:
        safe_name = file_system.sanitize_filename(new_file)
    except ValueError:
        safe_name = new_file

    file_system.on_download_started(_run_test_id, safe_name)
    size_bytes = os.path.getsize(file_path)
    file_system.on_download_complete(_run_test_id, safe_name, file_path)

    log_function_definition(wait_for_download, timeout=timeout, _run_test_id=_run_test_id)
    return json.dumps({
        "status": "success",
        "file_name": safe_name,
        "size_bytes": size_bytes,
    })


# ─── File Upload to Web Form (browser-specific) ─────────────────────────────

def upload_file_to_form(locator_type, locator, file_name, wait_for='', timeout=15000, _run_test_id='1') -> str:
    """
    Uploads an agent file to a web form's file input element using Selenium's send_keys().
    The locator must point to an <input type="file"> element.

    Args:
        locator_type: locator strategy (css, xpath, id)
        locator: the locator path for the file input element (must be input[type=file])
        file_name: name of the file in the agent's attachments directory
        wait_for: optional locator to wait for after upload (e.g. a success message).
                  Format: 'locator_type:locator' like 'css:.upload-success'
        timeout: max milliseconds to wait for upload completion (default 15000 = 15s)

    Usage:
        upload_file_to_form({'locator_type': 'css', 'locator': 'input[type=file]', 'file_name': 'my_file.txt'})
        upload_file_to_form({'locator_type': 'css', 'locator': '#file-input', 'file_name': 'report.pdf', 'wait_for': 'css:.upload-done'})
    """
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = 15000

    try:
        safe_name = file_system.sanitize_filename(file_name)
    except ValueError as e:
        return f"error: {e}"

    attachments_dir = file_system.get_attachments_dir()
    file_path = (attachments_dir / safe_name).resolve()
    if not file_path.is_file():
        avail = [f.name for f in attachments_dir.resolve().iterdir() if f.is_file() and _is_valid_download(f.name)] if attachments_dir.exists() else []
        return f"error: file '{safe_name}' not found at {file_path}. Available files: {avail}"

    global driver
    abs_path = str(file_path)
    element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
    element.send_keys(abs_path)

    wait_detail = ""
    try:
        if wait_for:
            # Parse 'locator_type:locator' format
            parts = wait_for.split(':', 1)
            if len(parts) == 2:
                wf_type, wf_locator = parts
                driver[_run_test_id].e(
                    locator_type=wf_type, locator=wf_locator
                ).wait_until_exists(seconds=timeout // 1000)
                wait_detail = f", waited for '{wait_for}' to appear"
            else:
                wait_detail = f", warning: invalid wait_for format '{wait_for}' (expected 'locator_type:locator')"
        else:
            # Brief pause for the upload to process
            time.sleep(2)
            wait_detail = ", waited 2s for upload to process"
    except Exception as e:
        wait_detail = f", warning: wait timed out ({e}) - upload may still be in progress"

    log_function_definition(upload_file_to_form, locator_type, locator, file_name, _run_test_id=_run_test_id)
    return f"uploaded {safe_name} ({abs_path}) to {locator_type}={locator}{wait_detail}"


def select_by_visible_text(locator_type: str, locator: str, text: str, _run_test_id='1') -> str:
    """
    Selects a value in a <select> element by visible text.

    The element is defined by its locator_type (id, css, xpath)
    and its locator path associated.

    Usage:
        select_by_visible_text({'locator_type': 'css', 'locator': '#country', 'text': 'Latvia'})
        select_by_visible_text({'locator_type': 'xpath', 'locator': '//select[@name=\"role\"]', 'text': 'Admin'})
    """
    global driver
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.support.ui import Select
    from selenium.common.exceptions import StaleElementReferenceException

    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=DEFAULT_TIMEOUT)

    element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
    actions = ActionChains(driver[_run_test_id].get_driver())
    actions.move_to_element(element).perform()

    try:
        Select(element).select_by_visible_text(text)
    except StaleElementReferenceException:
        element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
        Select(element).select_by_visible_text(text)

    log_function_definition(select_by_visible_text, locator_type, locator, text, _run_test_id=_run_test_id)
    return f"selected value '{text}' successfully"


def select_by_value(locator_type: str, locator: str, value: str, _run_test_id='1') -> str:
    """
    Selects an option in a <select> element by its value attribute.

    Usage:
        select_by_value({'locator_type': 'css', 'locator': '#country', 'value': 'us'})
        select_by_value({'locator_type': 'xpath', 'locator': '//select[@name=\"role\"]', 'value': 'admin'})
    """
    global driver
    from selenium.webdriver.support.ui import Select
    from selenium.common.exceptions import StaleElementReferenceException

    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=DEFAULT_TIMEOUT)
    element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
    try:
        Select(element).select_by_value(value)
    except StaleElementReferenceException:
        element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
        Select(element).select_by_value(value)
    log_function_definition(select_by_value, locator_type, locator, value, _run_test_id=_run_test_id)
    return f"selected value '{value}' successfully"


def get_element_text(locator_type: str, locator: str, _run_test_id='1') -> str:
    """
    Returns the visible text content of an element.

    Usage:
        get_element_text({'locator_type': 'css', 'locator': '.status-message'})
        get_element_text({'locator_type': 'xpath', 'locator': '//h1'})
    """
    global driver
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=DEFAULT_TIMEOUT)
    element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
    text = element.text
    log_function_definition(get_element_text, locator_type, locator, _run_test_id=_run_test_id)
    return text


def get_attribute(locator_type: str, locator: str, attribute: str, _run_test_id='1') -> str:
    """
    Returns the value of a specific HTML attribute of an element.

    Usage:
        get_attribute({'locator_type': 'css', 'locator': 'a.link', 'attribute': 'href'})
        get_attribute({'locator_type': 'css', 'locator': 'input#email', 'attribute': 'value'})
        get_attribute({'locator_type': 'css', 'locator': '#btn', 'attribute': 'disabled'})
    """
    global driver
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=DEFAULT_TIMEOUT)
    element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
    value = element.get_attribute(attribute)
    log_function_definition(get_attribute, locator_type, locator, attribute, _run_test_id=_run_test_id)
    return value if value is not None else ''


def press_key(locator_type: str, locator: str, key: str, _run_test_id='1') -> str:
    """
    Sends a keyboard key to an element.

    Supported keys: ENTER, TAB, ESCAPE, SPACE, BACKSPACE, DELETE,
                    ARROW_UP, ARROW_DOWN, ARROW_LEFT, ARROW_RIGHT,
                    HOME, END, PAGE_UP, PAGE_DOWN.

    Usage:
        press_key({'locator_type': 'css', 'locator': '#search', 'key': 'ENTER'})
        press_key({'locator_type': 'css', 'locator': '#field', 'key': 'TAB'})
    """
    global driver
    from selenium.webdriver.common.keys import Keys
    key_map = {
        'ENTER': Keys.ENTER, 'TAB': Keys.TAB,
        'ESCAPE': Keys.ESCAPE, 'ESC': Keys.ESCAPE,
        'SPACE': Keys.SPACE, 'BACKSPACE': Keys.BACKSPACE, 'DELETE': Keys.DELETE,
        'ARROW_UP': Keys.ARROW_UP, 'ARROW_DOWN': Keys.ARROW_DOWN,
        'ARROW_LEFT': Keys.ARROW_LEFT, 'ARROW_RIGHT': Keys.ARROW_RIGHT,
        'HOME': Keys.HOME, 'END': Keys.END,
        'PAGE_UP': Keys.PAGE_UP, 'PAGE_DOWN': Keys.PAGE_DOWN,
    }
    resolved_key = key_map.get(key.upper(), key)
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=DEFAULT_TIMEOUT)
    element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
    element.send_keys(resolved_key)
    log_function_definition(press_key, locator_type, locator, key, _run_test_id=_run_test_id)
    return f"pressed {key}"


def handle_alert(action: str = 'accept', text: str = '', _run_test_id='1') -> str:
    """
    Handles a JavaScript alert, confirm, or prompt dialog.

    Args:
        action: 'accept' (click OK), 'dismiss' (click Cancel),
                'get_text' (read the alert message),
                'send_keys' (type into a prompt then accept)
        text: text to type when action is 'send_keys'

    Usage:
        handle_alert({'action': 'accept'})
        handle_alert({'action': 'dismiss'})
        handle_alert({'action': 'get_text'})
        handle_alert({'action': 'send_keys', 'text': 'my input'})
    """
    global driver
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    selenium_driver = driver[_run_test_id].get_driver()
    alert = WebDriverWait(selenium_driver, DEFAULT_TIMEOUT).until(EC.alert_is_present())
    if action == 'get_text':
        result = alert.text
        log_function_definition(handle_alert, action, text, _run_test_id=_run_test_id)
        return result
    elif action == 'send_keys':
        alert.send_keys(text)
        alert.accept()
        log_function_definition(handle_alert, action, text, _run_test_id=_run_test_id)
        return "sent keys and accepted alert"
    elif action == 'dismiss':
        alert.dismiss()
        log_function_definition(handle_alert, action, text, _run_test_id=_run_test_id)
        return "alert dismissed"
    else:
        alert.accept()
        log_function_definition(handle_alert, action, text, _run_test_id=_run_test_id)
        return "alert accepted"


def hover(locator_type: str, locator: str, _run_test_id='1') -> str:
    """
    Hovers the mouse over an element without clicking.
    Useful for triggering dropdown menus or tooltips.

    Usage:
        hover({'locator_type': 'css', 'locator': '#nav-menu'})
        hover({'locator_type': 'xpath', 'locator': '//li[@class=\"dropdown\"]'})
    """
    global driver
    from selenium.webdriver.common.action_chains import ActionChains
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=DEFAULT_TIMEOUT)
    element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
    ActionChains(driver[_run_test_id].get_driver()).move_to_element(element).perform()
    log_function_definition(hover, locator_type, locator, _run_test_id=_run_test_id)
    return "hovered"


# Wrap all public functions with error sanitization so webapp users never see
# raw tracebacks or server file paths in error messages.
_mod = sys.modules[__name__]
for _name in list(vars(_mod)):
    if _name.startswith('_'):
        continue
    _fn = getattr(_mod, _name)
    if callable(_fn) and not isinstance(_fn, type) and inspect.isfunction(_fn):
        setattr(_mod, _name, _error_sanitizer(_fn))
del _mod, _name, _fn
