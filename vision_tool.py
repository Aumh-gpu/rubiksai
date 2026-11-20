import pyautogui
import PIL.Image
import os

from rubiks_core import llm  # google-genai, key handling and model fallback live here

def get_active_windows() -> str:
    """Returns a list of all currently open window titles, revealing all running apps and browser tabs."""
    try:
        import ctypes
        from ctypes import wintypes
        
        user32 = ctypes.windll.user32
        titles = []
        
        def _enum_callback(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buff, length + 1)
                    title = buff.value.strip()
                    if title:
                        titles.append(title)
            return True
        
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(_enum_callback), 0)
        
        if not titles:
            return "No visible windows found."
        return "Active Windows & Tabs:\n" + "\n".join([f"- {t}" for t in titles])
    except Exception as e:
        return f"Failed to get active windows: {str(e)}"

def analyze_screen(question: str = "Describe what is currently visible on the screen in detail. If a browser is open, explicitly list the visible tabs.", previous_context: str = "") -> str:
    """Takes a screenshot of the user's computer screen and uses Gemini Vision to answer the question about what is on it."""
    screenshot_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "current_screen.png")
    img = None
    try:
        try:
            import mss
            with mss.mss() as sct:
                sct.shot(mon=-1, output=screenshot_path)
        except Exception:
            # Fallback to pyautogui if mss is missing or fails
            import pyautogui
            pyautogui.screenshot(screenshot_path)
            
        img = PIL.Image.open(screenshot_path)
        img.load()

        prompt = f"You are the visual cortex for an AI assistant. Analyze this screenshot of the user's computer. Answer this specific query based on the screen: {question}"
        if previous_context:
            prompt += f"\n\nFor context, here is what the screen looked like previously. Notice any changes:\n{previous_context}"

        response = llm.generate([prompt, img])
        
        return f"Screen Analysis Result: {response.text}"
    except Exception as e:
        return f"Failed to capture or analyze the screen: {str(e)}"
    finally:
        # Clean up the screenshot so we don't clutter the drive
        try:
            if img:
                img.close()
            if os.path.exists(screenshot_path):
                os.remove(screenshot_path)
        except Exception:
            pass

def click_on_screen(description: str) -> str:
    """Finds an element on the screen matching the description and clicks it."""
    import re
    screenshot_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "click_target.png")
    img = None
    try:
        try:
            import mss
            with mss.mss() as sct:
                sct.shot(mon=-1, output=screenshot_path)
        except Exception:
            import pyautogui
            pyautogui.screenshot(screenshot_path)
            
        img = PIL.Image.open(screenshot_path)
        
        img.load()
        screen_w, screen_h = pyautogui.size()

        prompt = f"Return ONLY the bounding box [ymin, xmin, ymax, xmax] scaled 0-1000 for the UI element matching this description: {description}. If multiple match, pick the most relevant one. Do not output any other text."
        response = llm.generate([prompt, img])
        
        match = re.search(r'\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]', response.text)
        if not match:
            return f"Failed to find '{description}'. Vision AI returned: {response.text}"
            
        ymin, xmin, ymax, xmax = map(int, match.groups())
        
        center_x = int(((xmin + xmax) / 2000) * screen_w)
        center_y = int(((ymin + ymax) / 2000) * screen_h)
        
        pyautogui.moveTo(center_x, center_y, duration=0.5)
        pyautogui.click()
        
        return f"Successfully clicked on '{description}' at ({center_x}, {center_y})."
    except Exception as e:
        return f"Failed to click on screen: {str(e)}"
    finally:
        try:
            if img: img.close()
            if os.path.exists(screenshot_path): os.remove(screenshot_path)
        except:
            pass

