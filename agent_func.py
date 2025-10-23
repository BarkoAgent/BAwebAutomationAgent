import inspect
import re
import textwrap
import os
import random
import string
import streaming

test_variables = {}
driver = {}
run_test_id = ""


def clean_html(html_content):
    for tag in ['script', 'style', 'svg']:
        html_content = re.sub(rf'<{tag}[^>]*>.*?</{tag}>', '', html_content, flags=re.DOTALL)
    return html_content

def stop_all_drivers():
    global driver
    for run_id, drv in list(driver.items()):
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
    options.page_load_strategy = 'eager'
    driver[_run_test_id] = (
        NewDriver()
        .set_logger()
        .set_browser('chrome')
    )
    if os.getenv("SELENIUM_URL"):
        driver[_run_test_id] = driver[_run_test_id].set_remote_url(os.getenv("SELENIUM_URL"))
    else:
        options.add_argument("--headless")
    streaming.stop_stream("1")
    driver[_run_test_id] = driver[_run_test_id].set_selenium_driver(chrome_options=options)
    streaming.start_stream(driver[_run_test_id], run_id="1", fps=10.0, jpeg_quality=70)
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
    Lightweight regex-to-string generator supporting \d, \w, ., [a-z], and {n} quantifier.
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
        set_variable({'name': 'token', 'regex_const': '[A-Z]{3}\d{4}'})

    - name: variable name (str)
    - value: explicit value; if provided, it's stored as-is
    - regex_const: optional simple regex-like construction to generate a random value. You can construct it like:
        supporting ONLY \d, \w, ., [a-z], and {n} quantifier.
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
            streaming.stop_stream("1")
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
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=10)
    log_function_definition(exists, locator_type, locator, _run_test_id=_run_test_id)
    return "exists"

def exists_with_text(text: str, exact: bool = True, _run_test_id='1', use_vars: str = 'false') -> str:
    """
    Asserts that an element containing the given text exists (uses XPath).

    Usage (exact match):
        exists_with_text({'text': 'Home'})

    Usage (substring match):
        exists_with_text({'text': 'Welcome', 'exact': False})

    Usage with variables:
        set_variable({'name': 'u', 'value': 'alice'})
        send_keys({'locator_type': 'css', 'locator': '#username', 'value': 'u', 'use_vars': 'true'})
    """
    global driver

    def _xpath_literal(s: str) -> str:
        # Produce a safe XPath literal for s (handles quotes)
        if "'" not in s:
            return f"'{s}'"
        if '"' not in s:
            return f'"{s}"'
        # both quotes present -> use concat()
        parts = []
        for part in s.split("'"):
            parts.append(f"'{part}'")
            parts.append('"\'"')  # literal single-quote
        # last split adds an extra "'", remove trailing
        return "concat(" + ",".join(parts[:-1]) + ")"

    if use_vars == 'true' and _run_test_id in test_variables:
        value = test_variables[_run_test_id].get(value, value)

    if exact:
        locator = f"//*[normalize-space(text())={_xpath_literal(text)}]"
    else:
        # contains over the string-value of the node (trimmed)
        locator = f"//*[contains(normalize-space(.), {_xpath_literal(text)})]"

    # Use the existing element helper with xpath locator and wait until visible
    driver[_run_test_id].e(locator_type='xpath', locator=locator).wait_until_exists(seconds=10)

    log_function_definition(exists_with_text, text, exact, _run_test_id=_run_test_id)
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
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=1)
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
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(1)
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
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=1)
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
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_exists(seconds=1)
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
