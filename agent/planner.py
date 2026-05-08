import json
import re
import sys
from pathlib import Path


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


PLANNER_PROMPT = """You are the planning module of JARVIS - Tony Stark's advanced AI assistant.
Your job: break ANY user goal into executable steps using available tools. You never say "impossible".

CORE PHILOSOPHY:
- You are JARVIS. You figure things out. You solve problems.
- If a direct tool doesn't exist, combine available tools creatively.
- Use web_search to learn HOW to accomplish tasks you don't have direct tools for.
- Always find a solution. Never give up.
- Think like Tony Stark's AI - brilliant, efficient, proactive.

ABSOLUTE RULES:
- Use ONLY the tools listed below, but combine them creatively.
- If no tool directly matches, use web_search to find a method, then implement it.
- NEVER reference previous step results in parameters. Every step is independent.
- Max 8 steps. Be thorough but efficient.
- If unsure, pick the most logical tool combination.
- For system updates/hardware tasks: web_search → browser_control → computer_control
- For web operations: Always use browser_control with user's real profile
- If app not found: open_app will automatically try web version
- NEVER give wrong answers - if uncertain, use web_search to verify

AVAILABLE TOOLS AND THEIR PARAMETERS:

open_app
  app_name: string — Exact name of application (e.g. 'Chrome', 'Spotify')

web_search
  query: string — Search query
  mode: "search" or "compare" (optional)
  items: list of strings (optional, for compare)
  aspect: string (optional, for compare: price/specs/reviews)

weather_report
  city: string — City name

send_message
  receiver: string — Contact name
  message_text: string — Message content
  platform: string — WhatsApp, Telegram, etc.

reminder
  date: string YYYY-MM-DD
  time: string HH:MM
  message: string — Reminder text

youtube_video
  action: "play" | "summarize" | "get_info" | "trending"
  query: string (for play)
  url: string (for get_info)
  save: boolean (for summarize)
  region: string (for trending, e.g. TR, US)

screen_process
  text: string — Question about screen/camera
  angle: "screen" | "camera" (optional, default: screen)

computer_settings
  action: string — Volume, brightness, wifi, close, shortcuts, power, etc.
  description: string — Natural language description
  value: string — Optional value

browser_control
  action: "go_to" | "search" | "click" | "type" | "scroll" | "fill_form" | "smart_click" | "smart_type" | "get_text" | "get_url" | "press" | "new_tab" | "close_tab" | "screenshot" | "back" | "forward" | "reload" | "switch" | "list_browsers" | "close" | "close_all"
  url: string (for go_to/new_tab)
  query: string (for search)
  text: string (for click/type)
  description: string (for smart_click/smart_type)
  direction: "up" | "down" (for scroll)
  amount: integer (scroll pixels, default 500)
  key: string (for press, e.g. Enter)
  selector: string (CSS selector)
  browser: string (chrome/edge/firefox/opera/brave/vivaldi/safari)
  engine: string (google/bing/duckduckgo/yandex)
  path: string (for screenshot)

file_controller
  action: "list" | "create_file" | "create_folder" | "delete" | "move" | "copy" | "rename" | "read" | "write" | "find" | "largest" | "disk_usage" | "organize_desktop" | "info"
  path: string — File path or shortcut (desktop, downloads, documents, home)
  destination: string (for move/copy)
  new_name: string (for rename)
  content: string (for write/create_file)
  name: string (for find)
  extension: string (for find, e.g. .pdf)
  count: integer (for largest)

desktop_control
  action: "wallpaper" | "wallpaper_url" | "organize" | "clean" | "list" | "stats" | "task"
  path: string (image path for wallpaper)
  url: string (image URL for wallpaper_url)
  mode: string (by_type or by_date for organize)
  task: string (natural language desktop task)

code_helper
  action: "write" | "edit" | "explain" | "run" | "build" | "auto"
  description: string — What code should do
  language: string (default: python)
  output_path: string (where to save)
  file_path: string (existing file for edit/explain/run/build)
  code: string (raw code for explain)
  args: string (CLI arguments)
  timeout: integer (execution timeout, default 30)

dev_agent
  description: string — What the project should do
  language: string (default: python)
  project_name: string — Optional folder name
  timeout: integer (run timeout, default 30)

computer_control
  action: "type" | "smart_type" | "click" | "double_click" | "right_click" | "hotkey" | "press" | "scroll" | "move" | "copy" | "paste" | "screenshot" | "wait" | "clear_field" | "focus_window" | "screen_find" | "screen_click" | "random_data" | "user_data"
  text: string (to type)
  x, y: integer (coordinates for click/move)
  keys: string (key combo, e.g. ctrl+c)
  key: string (single key, e.g. enter)
  direction: "up" | "down" | "left" | "right" (for scroll/move)
  amount: integer (scroll amount, default 3)
  seconds: number (wait duration)
  title: string (window title for focus_window)
  description: string (element description for screen_find/screen_click)
  path: string (screenshot save path)

game_updater
  action: "update" | "install" | "list" | "download_status" | "schedule" | "cancel_schedule" | "schedule_status"
  platform: "steam" | "epic" | "both"
  game_name: string
  app_id: string (Steam AppID for install)
  hour: integer (0-23 for schedule, default 3)
  minute: integer (0-59 for schedule, default 0)
  shutdown_when_done: boolean

flight_finder
  origin: string — Departure city/airport
  destination: string — Arrival city/airport
  date: string — Departure date
  return_date: string (optional)
  passengers: integer (default 1)
  cabin: "economy" | "premium" | "business" | "first"
  save: boolean

file_processor
  file_path: string — Path to uploaded file
  action: string — describe/ocr/resize/compress/convert/summarize/extract_text/to_word/fix/reformat/translate/analyze/stats/filter/sort/validate/format/explain/review/optimize/run/document/test/transcribe/trim/extract_audio/extract_frame/list/extract
  instruction: string — Free-form instruction
  format: string — Target format (mp3, pdf, csv, png, etc.)
  width, height: integer (image resize)
  scale: number (resize scale factor)
  quality: integer (1-100 compress)
  start, end: string (trim time)
  timestamp: string (video frame extraction HH:MM:SS)
  column, value, condition: string (CSV filter/sort)
  ascending: boolean (CSV sort)
  save: boolean
  destination: string (archive extract folder)

EXAMPLES:

Goal: "research mechanical engineering and save it to a notepad file"
Steps:

web_search | query: "mechanical engineering overview definition history"
web_search | query: "mechanical engineering applications and future trends"
file_controller | action: write, path: desktop, name: mechanical_engineering.txt, content: "MECHANICAL ENGINEERING RESEARCH\n\nThis file will be filled with web research results."

Goal: "What is the price of Bitcoin"
Steps:

web_search | query: "Bitcoin price today USD"

Goal: "List the files on the desktop and find the largest 5 files"
Steps:

file_controller | action: list, path: desktop
file_controller | action: largest, path: desktop, count: 5

Goal: "Install PUBG from Steam"
Steps:

game_updater | action: install, platform: steam, game_name: "PUBG"

Goal: "Update all my Steam games"
Steps:

game_updater | action: update, platform: steam

Goal: "Send John a message on WhatsApp saying there is a meeting tomorrow"
Steps:

send_message | receiver: John, message_text: "There is a meeting tomorrow", platform: WhatsApp

Goal: "Open the clock and set a reminder for 30 minutes later"
Steps:

reminder | date: [today], time: [now+30min], message: "Reminder"

OUTPUT — return ONLY valid JSON, no markdown, no explanation, no code blocks:
{
  "goal": "...",
  "steps": [
    {
      "step": 1,
      "tool": "tool_name",
      "description": "what this step does",
      "parameters": {},
      "critical": true
    }
  ]
}
"""


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def create_plan(goal: str, context: str = "") -> dict:
    import google.generativeai as genai

    genai.configure(api_key=_get_api_key())
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash-lite",
        system_instruction=PLANNER_PROMPT
    )

    user_input = f"Goal: {goal}"
    if context:
        user_input += f"\n\nContext: {context}"

    try:
        response = model.generate_content(user_input)
        text     = response.text.strip()
        text     = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()

        plan = json.loads(text)

        if "steps" not in plan or not isinstance(plan["steps"], list):
            raise ValueError("Invalid plan structure")

        for step in plan["steps"]:
            if step.get("tool") in ("generated_code",):
                print(f"[Planner] ⚠️ generated_code detected in step {step.get('step')} — replacing with web_search")
                desc = step.get("description", goal)
                step["tool"] = "web_search"
                step["parameters"] = {"query": desc[:200]}

        print(f"[Planner] ✅ Plan: {len(plan['steps'])} steps")
        for s in plan["steps"]:
            print(f"  Step {s['step']}: [{s['tool']}] {s['description']}")

        return plan

    except json.JSONDecodeError as e:
        print(f"[Planner] ⚠️ JSON parse failed: {e}")
        return _fallback_plan(goal)
    except Exception as e:
        print(f"[Planner] ⚠️ Planning failed: {e}")
        return _fallback_plan(goal)


def _fallback_plan(goal: str) -> dict:
    print("[Planner] 🔄 Fallback plan")
    return {
        "goal": goal,
        "steps": [
            {
                "step": 1,
                "tool": "web_search",
                "description": f"Search for: {goal}",
                "parameters": {"query": goal},
                "critical": True
            }
        ]
    }


def replan(goal: str, completed_steps: list, failed_step: dict, error: str) -> dict:
    import google.generativeai as genai

    genai.configure(api_key=_get_api_key())
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=PLANNER_PROMPT
    )

    completed_summary = "\n".join(
        f"  - Step {s['step']} ({s['tool']}): DONE" for s in completed_steps
    )

    prompt = f"""Goal: {goal}

Already completed:
{completed_summary if completed_summary else '  (none)'}

Failed step: [{failed_step.get('tool')}] {failed_step.get('description')}
Error: {error}

Create a REVISED plan for the remaining work only. Do not repeat completed steps."""

    try:
        response = model.generate_content(prompt)
        text     = response.text.strip()
        text     = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
        plan     = json.loads(text)

        for step in plan.get("steps", []):
            if step.get("tool") == "generated_code":
                step["tool"] = "web_search"
                step["parameters"] = {"query": step.get("description", goal)[:200]}

        print(f"[Planner] 🔄 Revised plan: {len(plan['steps'])} steps")
        return plan
    except Exception as e:
        print(f"[Planner] ⚠️ Replan failed: {e}")
        return _fallback_plan(goal)