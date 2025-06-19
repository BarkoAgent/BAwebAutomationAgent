import inspect
import re
import textwrap
import os

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
    Writes a transformed version of the function's source code to `function_calls.py`, removing:
      1. The `def function_name(...):` line
      2. Any `log_function_definition(...)` lines
      3. Replacing param=param with param="actual_value" for each argument actually passed
      4. Removing indentation
      6. Removing lines that start with 'return' (i.e., removing all return statements)
      7. Removing any triple-quoted docstrings, such as:
         \"\"\"
         This code is used like ...
         \"\"\"
    """

    # 1. Grab the original function source
    source = inspect.getsource(fn)

    # 2. Split into lines
    lines = source.splitlines()

    # 3. Remove the first line if it starts with 'def '
    trimmed_lines = []
    skip_first_def = True
    for line in lines:
        if skip_first_def and line.strip().startswith("def "):
            skip_first_def = False
            continue
        trimmed_lines.append(line)

    # 4. Remove lines containing `log_function_definition(...)`,
    #    lines that start with 'return', and lines that start with 'global'
    #    Also track and remove triple-quoted docstrings.
    filtered_lines = []
    in_docstring = False
    for line in trimmed_lines:
        stripped_line = line.strip()

        # Toggle docstring detection whenever we see triple quotes
        if '"""' in stripped_line:
            in_docstring = not in_docstring
            # We skip the line that has the triple quotes too
            continue

        # If we are inside a triple-quoted docstring, skip all those lines
        if in_docstring:
            continue

        # remove lines that call log_function_definition(...)
        if "log_function_definition(" in line:
            continue
        if "stop_all_drivers()" in line:
            continue
        # remove lines that call log_function_definition(...)
        if "clean_html(" in line:
            continue
        # remove lines that start with "return"
        if stripped_line.startswith("return"):
            continue
        # remove lines that start with "global"
        if stripped_line.startswith("global"):
            continue

        filtered_lines.append(line)

    # 5. Replace references to parameters with actual values, e.g. param=param -> param="actual"
    from inspect import signature
    sig = signature(fn)
    param_names = list(sig.parameters.keys())

    # Pair them up: param -> actual_value
    param_values = {}
    # We'll assume *args align in order to param_names
    for name, val in zip(param_names, args):
        param_values[name] = val
    # And also handle any named arguments in **kwargs
    for k, v in kwargs.items():
        param_values[k] = v

    replaced_lines = []
    for line in filtered_lines:
        new_line = line

        # (A) Replace param=param with param="actual_val"
        for param, actual_val in param_values.items():
            pattern = rf"\b{param}={param}\b"
            repl = f'{param}={repr(actual_val)}'
            new_line = re.sub(pattern, repl, new_line)
        
        if "selenium_url" in new_line and kwargs['selenium_url'] is not None:
            new_line = new_line.replace("selenium_url", repr(kwargs['selenium_url']))
        # (A) Replace driver[_run_test_id] with driver
        pattern = r"driver\[_run_test_id\]"
        repl = f'driver'
        new_line = re.sub(pattern, repl, new_line)

        replaced_lines.append(new_line)

    # 6. Dedent (remove indentation)
    dedented_code = textwrap.dedent("\n".join(replaced_lines)).rstrip() + "\n"

    # Ensure the 'tests' directory exists; if not, create it.
    directory = "tests"
    if not os.path.exists(directory):
        os.makedirs(directory)

    # Build the file path using os.path.join for portability.
    file_path = os.path.join(directory, f"function_calls{kwargs['_run_test_id']}.py")

    # 7. Append everything to function_calls.py in the tests directory
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(dedented_code)

###############################################################################
# Below are your selenium-related functions
###############################################################################
def create_driver(_run_test_id='1'):
    """
    Creates a driver that is necessary for the automation to run in the first place.
    Doesn't need any input values to the function and it returns success as string
    """
    # pass no arguments
    global driver
    stop_all_drivers()
    from selenium.webdriver.chrome.options import Options
    from testui.support.appium_driver import NewDriver
    options = Options()
    options.add_argument("disable-user-media-security")
    driver[_run_test_id] = (
        NewDriver()
        .set_logger()
        .set_browser('chrome')
        .set_selenium_driver(chrome_options=options)
    )
    driver[_run_test_id].navigate_to("https://google.com")
    log_function_definition(create_driver, _run_test_id=_run_test_id)
    return "driver created"


def stop_driver(_run_test_id='1'):
    """
    Stops the driver so that later another one can take place,
    and it's always run at the end of the test case.
    Doesn't need any input values to the function and it returns success as string
    """
    global driver
    driver[_run_test_id].quit()
    log_function_definition(stop_driver, _run_test_id=_run_test_id)
    return "success"


def maximize_window(_run_test_id='1'):
    """
    Maximizes the window to the biggest size of the screen.
    :return Success maximizing
    """
    global driver
    driver[_run_test_id].get_driver().maximize_window()
    log_function_definition(maximize_window, _run_test_id=_run_test_id)
    return "success maximizing"


def add_cookie(name, value, _run_test_id='1'):
    """
    adds cookie by name and value
    :return "Cookies added"
    """
    global driver
    name=name
    value=value
    driver[_run_test_id].get_driver().add_cookie({'name': name, 'value': value})
    log_function_definition(add_cookie, name, value, _run_test_id=_run_test_id)
    return "Cookies added"


def navigate_to_url(url: str, _run_test_id='1') -> str:
    """
    Navigates the browser to a specific URL (the only parameter should have the format `https://...`).
    Returns the url.
    """
    global driver
    driver[_run_test_id].navigate_to(url=url)

    log_function_definition(navigate_to_url, url, _run_test_id=_run_test_id)
    return url


def send_keys(locator_type: str, locator: str, value: str, _run_test_id='1') -> str:
    """
    Types in the value in the element defined by its `locator_type` (id, css, xpath)
    and its locator path associated.
    """
    global driver
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).send_keys(value=value)
    log_function_definition(send_keys, locator_type, locator, value, _run_test_id=_run_test_id)
    return "sent keys"


def exists(locator_type: str, locator: str, _run_test_id='1') -> str:
    """
    checks if element defined by its `locator_type` (id, css, xpath)
    and its locator path associated exists in the web page
    """
    global driver
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).wait_until_visible(seconds=10)
    log_function_definition(exists, locator_type, locator, _run_test_id=_run_test_id)
    return "exists"

def does_not_exist(locator_type: str, locator: str, _run_test_id='1') -> str:
    """
    checks if element defined by its `locator_type` (id, css, xpath)
    and its locator path associated does NOT exist in the web page
    """
    global driver
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).no().wait_until_visible(seconds=10)
    log_function_definition(does_not_exist, locator_type, locator, _run_test_id=_run_test_id)
    return "doesn't exists"

def scroll_to_element(locator_type: str, locator: str, _run_test_id='1') -> str:
    """
    scrolls until the element defined by its `locator_type` (id, css, xpath)
    and its locator path associated is visible in the web page
    """
    global driver
    element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
    current_driver_instance = driver[_run_test_id]
    current_driver_instance.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});", element)
    log_function_definition(scroll_to_element, locator_type, locator, _run_test_id=_run_test_id)
    return "scrolled"

def click(locator_type: str, locator: str, _run_test_id='1') -> str:
    """
    Clicks in the element defined by its `locator_type` (id, css, xpath)
    and its locator path associated.

    It will return the HTML after clicking
    """
    global driver
    driver[_run_test_id].e(locator_type=locator_type, locator=locator).click()
    log_function_definition(click, locator_type, locator, _run_test_id=_run_test_id)
    content = driver[_run_test_id].get_driver().page_source
    html_content = clean_html(content)
    return html_content

def double_click(locator_type: str, locator: str, _run_test_id='1') -> str:
    """
    Double clicks on the element defined by its `locator_type` (id, css, xpath)
    and its locator path associated.
    """
    global driver
    from selenium.webdriver.common.action_chains import ActionChains
    element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
    actions = ActionChains(driver[_run_test_id])
    actions.double_click(element).perform()
    log_function_definition(double_click, locator_type, locator, _run_test_id=_run_test_id)
    return "double clicked"

def right_click(locator_type: str, locator: str, _run_test_id='1') -> str:
    """
    Right clicks on the element defined by its `locator_type` (id, css, xpath)
    and its locator path associated.
    """
    global driver
    from selenium.webdriver.common.action_chains import ActionChains
    element = driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element()
    actions = ActionChains(driver[_run_test_id])
    actions.context_click(element).perform()
    log_function_definition(right_click, locator_type, locator, _run_test_id=_run_test_id)
    return "right clicked"

def get_page_html(_run_test_id='1') -> str:
    """
    Returns the full HTML of the page (cleaned) so that you can parse it for exists or
    click, send_keys, etc. on some element.
    """
    global driver
    content = driver[_run_test_id].get_driver().page_source
    html_content = clean_html(content)
    return html_content


def return_current_url(_run_test_id='1') -> str:
    """
    Returns the current URL of the page.
    """
    global driver
    return driver[_run_test_id].get_driver().current_url


def change_windows_tabs(_run_test_id='1') -> str:
    """
    Change the tab or window used to a different one. 
    It will return the new page html
    """
    global driver
    selenium_driver = driver[_run_test_id].get_driver()
    print("Parent window title: " + selenium_driver.title)
    p = selenium_driver.current_window_handle
    chwd = selenium_driver.window_handles
    for w in chwd:
        if(w!=p):
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

    :Args:
        - frame_name: The name of the window to switch to, an integer representing the index. 
        if its just a new tab, the original normally is 0 and the new one would be 1
    :Usage:
        ::
            change_tab('frame_name')
            change_tab(1)
    """
    global driver
    frame_name=frame_name
    driver[_run_test_id].switch_to.frame(frame_name, _run_test_id=_run_test_id)
    log_function_definition(change_frame_by_id, frame_name)
    return "frame_changed"

def change_frame_by_locator(locator_type: str = None, locator: str = None, _run_test_id='1') -> str:
    """
    Switches focus to the specified iframe, by the locator.

    :Args:
        - locator_type: str
        - locator: str:
    """
    global driver
    locator_type=locator_type
    locator=locator
    driver[_run_test_id].switch_to.frame(driver[_run_test_id].e(locator_type=locator_type, locator=locator).get_element())
    log_function_definition(change_frame_by_locator, locator_type, locator, _run_test_id=_run_test_id)
    return "frame_changed"


def change_frame_to_original(_run_test_id='1') -> str:
    """
    Switches focus to the original after using the change_frame. 
    Can be different Tab or iFrame.
    """
    # Notice we pass the actual values for all 3 parameters
    global driver
    driver[_run_test_id].switch_to.default_content()
    log_function_definition(change_frame_to_original, _run_test_id=_run_test_id)
    return "frame_changed"
