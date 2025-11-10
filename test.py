# --- ALL IMPORTS ---
# I've added cv2 here, as you pointed out!
import cv2 
import serial
import time
import sys
import numpy as np
import json
import threading
import requests
import speech_recognition as sr
import pyttsx3
import os
import webbrowser
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# --- CONFIGURATION & API SETUP ---

# --- GEMINI API (OFFLINE MODE) ---
# You can leave this as-is, we are not using it.
GEMINI_API_KEY = "AIzaSyDAr1goq9yRC52RePHGz1HUfn1X4A8upu0" 
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent"

# --- NEW: OLLAMA CONFIGURATION ---
# Set this to True to use your local Ollama, False to try Gemini
USE_OLLAMA = False 
# This is the default URL for a local Ollama server
OLLAMA_API_URL = "http://127.0.0.1:11434" 
# IMPORTANT: Change this to the model you have downloaded in Ollama
# --- UPDATED as per your request ---
OLLAMA_MODEL = "Gemma3:1b" 

# --- Path to your WebDriver ---
try:
    DRIVER_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), 'chromedriver.exe'))
except NameError:
    DRIVER_PATH = os.path.abspath('chromedriver.exe')
    
# --- AUTONOMOUS CAR CONFIGURATION (Merged) ---
CAR_SERIAL_PORT = 'COM14'
CAR_BAUD_RATE = 9600
BASE_SPEED = 150
KP_TURN = 0.5
MAX_TARGET_AREA_PERCENT = 40 
TURN_DEAD_ZONE = 30 

# --- NEW: ECG/HRV MONITOR CONFIGURATION ---
ECG_SERIAL_PORT = 'COM14' # !!! CHANGE THIS to your ECG's COM port !!!
ECG_BAUD_RATE = 9600     

# --- Global States ---
STRESS_DATA_FILE = 'stress_detection_log.json'
CHAT_LOG_FILE = 'chat_history.json'
CHAT_HISTORY = [] 

STRESS_LEVEL = "Normal"
BLINK_RATE = 0
global_running_flag = True

# --- Car Global States ---
car_currentState = "IDLE" 
car_tracker = None
car_serial_port = None
last_car_command = ""

# --- NEW: ECG Global States ---
ecg_serial_port = None
current_heart_rate = 0 
last_ecg_data = "Connecting..."

# --- CAR SERIAL COMMUNICATION ---
def init_car_serial():
    """Tries to connect to the Arduino on the specified port."""
    global car_serial_port
    try:
        car_serial_port = serial.Serial(CAR_SERIAL_PORT, CAR_BAUD_RATE, timeout=1)
        time.sleep(2) 
        print(f"[Car Control]: Successfully connected to Arduino on {CAR_SERIAL_PORT}")
        return True
    except serial.SerialException as e:
        print(f"[Car Control ERROR]: Could not open serial port {CAR_SERIAL_PORT}.")
        print("[Car Control]: Please check your connection and port number.")
        print("[Car Control]: The program will run without sending commands.")
        return False

def _send_command_to_serial(command):
    """(Internal) Sends the actual command string to the Arduino."""
    if car_serial_port and car_serial_port.is_open:
        try:
            full_command = command + '\n'
            car_serial_port.write(full_command.encode())
            print(f"[Car Control Sent]: {command}")
        except serial.SerialException as e:
            print(f"[Car Control ERROR]: Error writing to serial port: {e}")
    else:
        print(f"[Car Control Mock]: {command}")

def send_car_command(command):
    """
    Checks if the command is new before sending.
    This prevents flooding the Arduino with duplicate commands.
    """
    global last_car_command
    if command == last_car_command:
        return
    
    _send_command_to_serial(command)
    last_car_command = command

def clamp(value, min_val=-255, max_val=255):
    """Clamps a value between a min and max."""
    return max(min_val, min(value, max_val))


# --- NEW: ECG/HRV MONITOR FUNCTIONS ---

def init_ecg_serial():
    """Tries to connect to the ECG sensor on its specified port."""
    global ecg_serial_port, last_ecg_data
    try:
        ecg_serial_port = serial.Serial(ECG_SERIAL_PORT, ECG_BAUD_RATE, timeout=1)
        time.sleep(2) 
        print(f"[ECG Monitor]: Successfully connected to ECG on {ECG_SERIAL_PORT}")
        last_ecg_data = "Connected"
        return True
    except serial.SerialException as e:
        print(f"[ECG Monitor ERROR]: Could not open serial port {ECG_SERIAL_PORT}.")
        print("[ECG Monitor]: Please check connection/port. Running without ECG.")
        last_ecg_data = "Disconnected"
        return False

def ecg_data_reader_thread():
    """
    Runs in a separate thread, constantly reading data from the ECG.
    This prevents blocking the main (video) thread.
    """
    global last_ecg_data, current_heart_rate
    
    while global_running_flag:
        if ecg_serial_port and ecg_serial_port.is_open:
            try:
                line = ecg_serial_port.readline()
                if line:
                    decoded_line = line.decode('utf-8').strip()
                    
                    if decoded_line:
                        last_ecg_data = decoded_line
                        # print(f"[ECG Raw]: {decoded_line}") 
                        
                        # --- FUTURE STEP ---
                        # Here we will add code to parse the line,
                        # e.g., if decoded_line.startswith("BPM:"):
                        #    current_heart_rate = int(decoded_line.split(":")[1])
                        # ---------------------
            
            except serial.SerialException as e:
                print(f"[ECG Thread ERROR]: {e}")
                last_ecg_data = "Error"
                time.sleep(1) 
            except UnicodeDecodeError:
                pass
        else:
            time.sleep(1)
    
    print("[ECG Monitor]: ECG data reader thread stopped.")


# --- NEXO VOICE ASSISTANT CORE FUNCTIONS ---

def speak(text):
    """Nexo speaks the given text using local TTS."""
    try:
        engine = pyttsx3.init()
        engine.setProperty('rate', 160)
        print(f"\n[Nexo]: {text}")
        engine.say(text)
        engine.runAndWait()
    except Exception as e:
        print(f"[ERROR - TTS]: Could not speak: {text}. Error: {e}")

def listen():
    """Listens for the user's command."""
    r = sr.Recognizer()
    try:
        with sr.Microphone() as source:
            r.adjust_for_ambient_noise(source, duration=1)
            speak("Listening...")
            print("\n[Listening...]")
            try:
                audio = r.listen(source, timeout=5, phrase_time_limit=10)
                text = r.recognize_google(audio)
                print(f"[User]: {text}")
                return text
            except sr.WaitTimeoutError:
                print("[System]: No speech detected within timeout.")
                return None
            except sr.UnknownValueError:
                print("[System]: Could not understand audio.")
                return None
            except sr.RequestError as e:
                print(f"[System]: Google Speech Recognition service failed; {e}")
                return None
    except AttributeError:
        print("[ERROR - SR]: No microphone found. Please check your audio input devices.")
        speak("I can't seem to find a microphone. Please check your audio settings.")
        return None
    except Exception as e:
        print(f"[System Error - listen()]: {e}")
        return None

def execute_pc_command(command_text, driver):
    """
    Executes local PC commands based on the LLM's instruction.
    NOW uses Selenium to control a web driver.
    Returns the (potentially modified) driver object.
    """
    global global_running_flag
    command_text = command_text.lower().replace("action: ", "").strip()
    
    try:
        # --- NEW: Check if driver exists, if not, create it ---
        if driver is None and any(cmd in command_text for cmd in ["open", "spotify_play", "spotify_nature"]):
            if not os.path.exists(DRIVER_PATH):
                speak(f"Error: ChromeDriver not found. Please download chromedriver.exe and place it in the same folder as me.")
                print(f"[ERROR]: ChromeDriver not found at {DRIVER_PATH}")
                return None
            
            print("[System]: Starting Chrome web driver...")
            service = Service(executable_path=DRIVER_PATH)
            options = webdriver.ChromeOptions()
            driver = webdriver.Chrome(service=service, options=options)
            driver.implicitly_wait(5) 
            speak("I've opened a new browser window for you.")

        if "open youtube" in command_text:
            driver.get("https://www.youtube.com")
            speak("I've opened YouTube for you.")
            
        elif "open google" in command_text:
            driver.get("https://www.google.com")
            speak("I've opened Google for you.")
        
        elif "spotify_play" in command_text:
            song_name = command_text.replace("spotify_play", "").strip()
            if song_name:
                driver.get(f"https://open.spotify.com/search/{song_name}") # Corrected URL
                speak(f"Opening Spotify search for {song_name}.")
            else:
                speak("You need to tell me the name of the song.")
                
        elif "spotify_nature" in command_text:
            driver.get("https://open.spotify.com/playlist/37i9dQZF1DX4PP3e4LmMJU") 
            speak("Opening a calming nature playlist.")

        elif "click" in command_text:
            if driver is None:
                speak("I don't have a browser open to click in. Please open something first.")
                return driver
                
            try:
                element_name = command_text.replace("click", "").strip()
                if not element_name:
                    speak("What would you like me to click?")
                    return driver

                speak(f"Trying to click on '{element_name}'...")
                
                wait = WebDriverWait(driver, 10)
                target_element = wait.until(
                    EC.element_to_be_clickable(
                        (By.XPATH, f"//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{element_name}')] | "
                                   f"//a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{element_name}')] | "
                                   f"//div[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{element_name}')] | "
                                   f"//*[contains(@aria-label, '{element_name}')] | "
                                   f"//*[contains(@id, '{element_name}')]")
                    )
                )
                
                target_element.click()
                speak(f"Clicked on {element_name}.")
                
            except Exception as e:
                print(f"[ERROR - Selenium Click]: {e}")
                speak(f"Sorry, I couldn't find a clickable element called '{element_name}'.")

        elif "close browser" in command_text:
            if driver:
                speak("Closing the browser.")
                driver.quit()
                driver = None
            else:
                speak("I don't have a browser open to close.")
        
        elif "open notepad" in command_text or "open text editor" in command_text:
            speak("Launching the text editor.")
            try:
                if os.name == 'nt': # Windows
                    os.startfile("notepad.exe")
                elif sys.platform == 'darwin': # macOS
                    os.system("open -a TextEdit")
                else: # Linux
                    os.system("gedit")
            except Exception as e:
                speak(f"Sorry, I couldn't open the application. Error: {e}")

        elif "close assistant" in command_text or "stop listening" in command_text:
            speak("Understood. Shutting down the assistant now. Goodbye!")
            if driver:
                driver.quit() 
            return "EXIT"

    except Exception as e:
        print(f"[ERROR - execute_pc_command]: {e}")
        speak("I ran into an error trying to do that.")

    return driver


# --- NEXO BRAIN (ROUTER) ---

def nexo_brain(chat_history, stress_level):
    """
    Routes the request to either Gemini or Ollama based on the USE_OLLAMA flag.
    """
    if USE_OLLAMA:
        print("[Nexo Brain]: Routing to Ollama...")
        return nexo_brain_ollama(chat_history, stress_level)
    else:
        print("[Nexo Brain]: Routing to Gemini...")
        return nexo_brain_gemini(chat_history, stress_level)

# --- (HELPER) GEMINI BRAIN ---
def nexo_brain_gemini(chat_history, stress_level):
    """
    Communicates with the Gemini API for intelligent responses.
    """
    if not GEMINI_API_KEY or GEMINI_API_KEY == "YOUR_NEW_API_KEY_GOES_HERE":
        return "I am running without a Gemini API key. I can only process PC commands."

    system_prompt = f"""
    You are Nexo, a friendly, non-GUI, face-to-face voice assistant and stress relief coach. This is an ongoing conversation. Use the previous messages for context (e.g., if the user just opened Spotify, 'click search' refers to Spotify). Your primary goal is to converse naturally, teach subjects, and offer stress relief.
    The user's current stress level, determined by real-time blink analysis, is: **{stress_level}**.
    # --- NEW: Heart Rate Data ---
    The user's current Heart Rate (Beats Per Minute) is: **{current_heart_rate}**.
    The raw data from the ECG is: **{last_ecg_data}**.
    **Rules:**
    1.  **PC/Web Control:** If the user conversationally asks to open, click, or close something, you MUST respond with a single line containing only the keyword 'ACTION:' followed by the command. You must not add any other words.
        * Example for opening YouTube: `ACTION: OPEN YOUTUBE`
        * Example for playing nature sounds: `ACTION: SPOTIFY_NATURE`
        * Example for playing a song: `ACTION: SPOTIFY_PLAY <song_name_and_artist>`
        * **NEW:** Example for clicking an element: `ACTION: CLICK <element_text_or_name>` (e.g., ACTION: CLICK search, ACTION: CLICK play button)
        * **NEW:** Example for closing the browser: `ACTION: CLOSE BROWSER`
    2.  **Conversational/Teaching:** For all other queries, respond naturally and concisely, as if speaking.
        * **Stress Relief:** If the stress level is 'Moderate Stress' or 'High Stress', gently suggest a quick, simple stress relief technique (like a deep breath) BEFORE answering their actual query.
        * **Do NOT** include the 'ACTION:' prefix in conversational responses.
    3.  **Stress Reporting:** Do not tell the user their stress level or heart rate unless they explicitly ask, for example, "How stressed am I?" or "What is my heart rate?"
    4.  **Tone:** Always maintain a helpful, friendly, and non-judgmental tone.
    """
    
    payload = {
        "contents": chat_history,
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "tools": [{"google_search": {}}] 
    }

    try:
        response = requests.post(
            f"{GEMINI_API_URL}?key={GEMINI_API_KEY}",
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=10 
        )
        response.raise_for_status()
        result = response.json()
        
        if 'candidates' not in result or not result['candidates']:
            print(f"[ERROR - Gemini Response]: No candidates found. Response: {result}")
            return "I'm sorry, I couldn't formulate a response. Please try again."
            
        text = result['candidates'][0]['content']['parts'][0]['text']
        return text

    except requests.exceptions.RequestException as e:
        print("\n" + "="*60)
        print("--- REAL GEMINI ERROR (NETWORK/CONNECTION) ---")
        print("The connection to Google's server failed. This is a network, firewall, or connection issue.")
        print(f"Full Error: {e}")
        print("="*60 + "\n")
        return "I am currently unable to connect to my brain. Please check your internet connection or API key."
    except Exception as e:
        print("\n" + "="*60)
        print("--- REAL GEMINI ERROR (API KEY / OTHER) ---")
        print("The request failed. This might be a bad API key, permissions problem, or other issue.")
        print(f"Full Error: {e}")
        print("="*60 + "\n")
        return "I received an unexpected response from my server. Could you please try asking again?"

# --- (HELPER) NEW OLLAMA BRAIN ---
def nexo_brain_ollama(chat_history, stress_level):
    """
    Communicates with a LOCAL OLLAMA server for intelligent responses.
    """
    print(f"[Nexo Brain]: Connecting to Ollama at {OLLAMA_API_URL} with model {OLLAMA_MODEL}...")

    # 1. Re-create the same system prompt
    system_prompt = f"""
    You are Nexo, a friendly, non-GUI, face-to-face voice assistant and stress relief coach. This is an ongoing conversation. Use the previous messages for context (e.g., if the user just opened Spotify, 'click search' refers to Spotify). Your primary goal is to converse naturally, teach subjects, and offer stress relief.
    The user's current stress level, determined by real-time blink analysis, is: **{stress_level}**.
    # --- NEW: Heart Rate Data ---
    The user's current Heart Rate (Beats Per Minute) is: **{current_heart_rate}**.
    The raw data from the ECG is: **{last_ecg_data}**.
    **Rules:**
    1.  **PC/Web Control:** If the user conversationally asks to open, click, or close something, you MUST respond with a single line containing only the keyword 'ACTION:' followed by the command. You must not add any other words.
        * Example for opening YouTube: `ACTION: OPEN YOUTUBE`
        * Example for playing nature sounds: `ACTION: SPOTIFY_NATURE`
        * Example for playing a song: `ACTION: SPOTIFY_PLAY <song_name_and_artist>`
        * **NEW:** Example for clicking an element: `ACTION: CLICK <element_text_or_name>` (e.g., ACTION: CLICK search, ACTION: CLICK play button)
        * **NEW:** Example for closing the browser: `ACTION: CLOSE BROWSER`
    2.  **Conversational/Teaching:** For all other queries, respond naturally and concisely, as if speaking.
        * **Stress Relief:** If the stress level is 'Moderate Stress' or 'High Stress', gently suggest a quick, simple stress relief technique (like a deep breath) BEFORE answering their actual query.
        * **Do NOT** include the 'ACTION:' prefix in conversational responses.
    3.  **Stress Reporting:** Do not tell the user their stress level or heart rate unless they explicitly ask, for example, "How stressed am I?" or "What is my heart rate?"
    4.  **Tone:** Always maintain a helpful, friendly, and non-judgmental tone.
    """

    # 2. Format chat history for Ollama
    # We will combine the entire history into a single prompt string
    full_prompt_string = ""
    for message in chat_history:
        role = "User" if message['role'] == 'user' else 'Nexo'
        full_prompt_string += f"{role}: {message['parts'][0]['text']}\n"
    
    # 3. Create the Ollama payload
    payload = {
        "model": OLLAMA_MODEL,
        "system": system_prompt,
        "prompt": full_prompt_string, # Send the whole conversation history
        "stream": False # We want the full response at once
    }

    # 4. Make the request to the local Ollama server
    try:
        response = requests.post(
            OLLAMA_API_URL,
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=30 # Give Ollama more time, local models can be slower
        )
        response.raise_for_status()
        result = response.json()
        
        # 5. Parse Ollama's response
        text = result.get('response')
        if not text:
            print("[ERROR - Ollama Response]: Response was empty.")
            return "I'm sorry, I couldn't formulate a response from Ollama."

        print(f"[Ollama Response]: {text.strip()}")
        # Clean up the response: Ollama might add its own role prefix
        if text.strip().lower().startswith("nexo:"):
            text = text.strip()[5:].strip()
            
        return text.strip()

    except requests.exceptions.ConnectionError as e:
        print("\n" + "="*60)
        print("--- OLLAMA CONNECTION ERROR ---")
        print(f"Could not connect to: {OLLAMA_API_URL}")
        print(">>> Are you sure Ollama is running? Start it on your computer. <<<")
        print(f"Full Error: {e}")
        print("="*60 + "\n")
        return "I cannot connect to the Ollama server. Please make sure it is running on your computer."
    except Exception as e:
        print("\n" + "="*60)
        print(f"--- OLLAMA GENERAL ERROR ---")
        print(f"This could be a problem with the model name ('{OLLAMA_MODEL}') or the request.")
        print(f"Full Error: {e}")
        print("="*60 + "\n")
        return "I had an unknown error while talking to Ollama."


# --- STRESS DETECTION (VIDEO PROCESSING) FUNCTIONS ---
def load_stress_data():
    """Load existing stress data from JSON file"""
    try:
        with open(STRESS_DATA_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"stress_events": []}

def save_stress_event(stress_level, blink_rate):
    """Save stress event to JSON file"""
    stress_data = load_stress_data()
    event = {
        "timestamp": datetime.now().isoformat(),
        "stress_level": stress_level,
        "blink_rate": blink_rate,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "time": datetime.now().strftime("%H:%M:%S"),
        "detection_method": "Blink Rate Analysis"
    }
    
    if stress_level == "Moderate Stress":
        event["possible_causes"] = [
            "Agitation or Eye fatigue (High BPM)",
            f"Blink rate was {blink_rate}, which is above the 25 BPM threshold."
        ]
    elif stress_level == "High Stress":
        event["possible_causes"] = [
            "Intense cognitive load or Eye strain (Low BPM)",
            f"Blink rate was {blink_rate}, which is below the 12 BPM threshold."
        ]
    
    stress_data["stress_events"].append(event)
    
    with open(STRESS_DATA_FILE, 'w') as f:
        json.dump(stress_data, f, indent=4)
        print(f"\n[System Log]: Stress event logged: {stress_level} at {blink_rate} BPM.")

# --- CHAT HISTORY JSON FUNCTIONS ---
def load_chat_history():
    """Loads chat history from the JSON file."""
    try:
        with open(CHAT_LOG_FILE, 'r') as f:
            history = json.load(f)
            print(f"[System]: Loaded {len(history)} messages from chat history.")
            return history
    except (FileNotFoundError, json.JSONDecodeError):
        print("[System]: No chat history file found. Starting fresh.")
        return []

def save_chat_history():
    """Saves the current chat history to the JSON file."""
    global CHAT_HISTORY
    try:
        with open(CHAT_LOG_FILE, 'w') as f:
            json.dump(CHAT_HISTORY, f, indent=4)
    except Exception as e:
        print(f"[ERROR - Chat History]: Could not save chat history. {e}")


# --- Constants for EAR (Eye Aspect Ratio) ---
EYE_AR_CONSEC_FRAMES = 2 

def main_video_and_car_loop():
    """
    MERGED LOOP:
    Runs the main video capture for Stress Detection AND Car Control logic.
    """
    global STRESS_LEVEL, BLINK_RATE, global_running_flag
    global car_currentState, car_tracker, car_serial_port, last_car_command
    global last_ecg_data

    try:
        print("[System]: Loading OpenCV face and eye detectors...")
        face_cascade_path = os.path.join(cv2.data.haarcascades, 'haarcascade_frontalface_default.xml')
        eye_cascade_path = os.path.join(cv2.data.haarcascades, 'haarcascade_eye.xml')

        if not os.path.exists(face_cascade_path):
            raise FileNotFoundError(f"Could not find face cascade: {face_cascade_path}")
        if not os.path.exists(eye_cascade_path):
            raise FileNotFoundError(f"Could not find eye cascade: {eye_cascade_path}")

        face_cascade = cv2.CascadeClassifier(face_cascade_path)
        eye_cascade = cv2.CascadeClassifier(eye_cascade_path)
        
        print("[System]: OpenCV cascades loaded successfully.")

        # --- CAR: Attempt to connect to Arduino ---
        init_car_serial()
        send_car_command("S") 

        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            raise IOError("Cannot open webcam. Is it in use by another app?")
        
        ret, frame = cap.read()
        if not ret:
            raise IOError("Cannot read frame from webcam.")
            
        frame_height, frame_width = frame.shape[:2]
        frame_center_x = frame_width // 2
        max_safe_area = (frame_width * frame_height) * (MAX_TARGET_AREA_PERCENT / 100.0)

        # Stress Monitor Constants
        MINUTE_INTERVAL = 60 
        current_minute_blinks = 0
        minute_start_time = time.time()
        BLINK_COUNTER = 0 

        print("\n[System]: Starting Main Video Loop (Stress Monitor & Car Control)...")
        print("--- Autonomous Car Controls ---")
        print("  f - Select target to follow")
        print("  s - Stop and enter SPIN mode")
        print("  r - Reset to IDLE")
        print("  q - Quit (shared with assistant)")
        print("---------------------------------")
        
        while cap.isOpened() and global_running_flag:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.1) 
                continue
            
            frame = cv2.flip(frame, 1)
            
            # --- Handle Key Presses (Car + Quit) ---
            key = cv2.waitKey(30) & 0xFF

            if key == ord('q'):
                print("[System]: 'q' pressed. Shutting down.")
                global_running_flag = False
                send_car_command("S") 
                break
            
            elif key == ord('s'):
                print("[Car Control]: State change: STOPPED -> SPINNING")
                car_currentState = "SPINNING"
                car_tracker = None 
                send_car_command("R") 
            
            elif key == ord('r'):
                print("[Car Control]: State change: RESET -> IDLE")
                car_currentState = "IDLE"
                car_tracker = None
                send_car_command("S") 
            
            elif key == ord('f') and car_currentState == "IDLE":
                print("[Car Control]: State change: IDLE -> SELECTING")
                cv2.putText(frame, "Draw box and press ENTER", (30, 90), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                cv2.imshow("Nexo Assistant and Car Control", frame)
                
                bbox = cv2.selectROI("Nexo Assistant and Car Control", frame, fromCenter=False, showCrosshair=True)
                
                if bbox[2] > 0 and bbox[3] > 0:
                    car_tracker = cv2.TrackerCSRT_create()
                    car_tracker.init(frame, bbox)
                    car_currentState = "FOLLOWING"
                    print("[Car Control]: State change: SELECTING -> FOLLOWING")
                else:
                    print("[Car Control]: Selection cancelled.")
                    car_currentState = "IDLE"

            # --- 1. CAR: State Machine Logic ---
            if car_currentState == "FOLLOWING":
                if car_tracker is None:
                    car_currentState = "IDLE"
                    continue

                ok, bbox = car_tracker.update(frame)
                if ok:
                    p1 = (int(bbox[0]), int(bbox[1]))
                    p2 = (int(bbox[0] + bbox[2]), int(bbox[1] + bbox[3]))
                    cv2.rectangle(frame, p1, p2, (0, 255, 0), 2, 1)
                    
                    box_area = bbox[2] * bbox[3]
                    if box_area > max_safe_area:
                        car_currentState = "AVOIDING"
                        print("[Car Control]: State change: FOLLOWING -> AVOIDING")
                        send_car_command("S") 
                    else:
                        target_center_x = int(bbox[0] + bbox[2] / 2)
                        error = target_center_x - frame_center_x
                        
                        command_to_send = ""
                        left_speed = BASE_SPEED
                        right_speed = BASE_SPEED

                        if abs(error) < TURN_DEAD_ZONE:
                            command_to_send = f"M,{int(left_speed)},{int(right_speed)}"
                        else:
                            turn = KP_TURN * error
                            left_speed = clamp(BASE_SPEED + turn)
                            right_speed = clamp(BASE_SPEED - turn)
                            command_to_send = f"M,{int(left_speed)},{int(right_speed)}"
                        
                        send_car_command(command_to_send)
                        cv2.putText(frame, f"Car: FOLLOWING (L:{int(left_speed)}, R:{int(right_speed)})", (10, 90), 
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                else:
                    print("[Car Control]: Tracking failed, returning to IDLE")
                    car_currentState = "IDLE"
                    car_tracker = None
                    send_car_command("S")

            elif car_currentState == "AVOIDING":
                cv2.putText(frame, "Car: AVOIDING (Target too close!)", (10, 90), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                send_car_command("S") 
                
                if car_tracker is not None:
                    ok, bbox = car_tracker.update(frame)
                    if ok:
                        box_area = bbox[2] * bbox[3]
                        if box_area < (max_safe_area * 0.8):
                            print("[Car Control]: State change: AVOIDING -> FOLLOWING")
                            car_currentState = "FOLLOWING"
                    else:
                        car_currentState = "IDLE" 
                        car_tracker = None
                        send_car_command("S")

            elif car_currentState == "SPINNING":
                cv2.putText(frame, "Car: SPINNING", (10, 90), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 0), 2)
                send_car_command("R") 

            elif car_currentState == "IDLE":
                cv2.putText(frame, "Car: IDLE (Press 'f' to select)", (10, 90), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

            # --- 2. STRESS: Detection Logic (on the same frame) ---
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(50, 50))
            
            eyes_detected = False
            detected_faces = [] 
            detected_eyes = [] 

            for (x, y, w, h) in faces:
                detected_faces.append((x, y, w, h)) 
                if w > 0:
                    roi_gray = gray[y:y+h, x:x+w]
                    eyes = eye_cascade.detectMultiScale(roi_gray, scaleFactor=1.1, minNeighbors=4, minSize=(20, 20))
                    
                    if len(eyes) > 0:
                        eyes_detected = True
                        for (ex, ey, ew, eh) in eyes:
                            detected_eyes.append((x+ex, y+ey, ew, eh))
                    break 
            
            if eyes_detected:
                BLINK_COUNTER = 0
            else:
                BLINK_COUNTER += 1

            if BLINK_COUNTER == EYE_AR_CONSEC_FRAMES:
                current_minute_blinks += 1

            elapsed_time = time.time() - minute_start_time

            if elapsed_time >= MINUTE_INTERVAL:
                BLINK_RATE = current_minute_blinks
                
                if BLINK_RATE < 12: 
                    STRESS_LEVEL = "High Stress" 
                elif BLINK_RATE > 25: 
                    STRESS_LEVEL = "Moderate Stress"
                else:
                    STRESS_LEVEL = "Normal"
                    
                if STRESS_LEVEL != "Normal":
                    save_stress_event(STRESS_LEVEL, BLINK_RATE)
                    
                print(f"\n[Monitor]: 1-Min BPM: {BLINK_RATE} | Status: {STRESS_LEVEL}")
                current_minute_blinks = 0
                minute_start_time = time.time()

            # --- 3. COMBINED Drawing logic ---
            for (x, y, w, h) in detected_faces:
                cv2.rectangle(frame, (x, y), (x+w, y+h), (255, 0, 0), 2)
            for (x, y, w, h) in detected_eyes:
                cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 255, 0), 2)
                
            color = (0, 255, 0) # Green for Normal
            if STRESS_LEVEL == "Moderate Stress":
                color = (0, 165, 255) # Orange
            elif STRESS_LEVEL == "High Stress":
                color = (0, 0, 255) # Red
                
            cv2.putText(frame, f"Status: {STRESS_LEVEL}", (10, 30), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
            cv2.putText(frame, f"Blink Rate (BPM): {BLINK_RATE}", (10, 60), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

            # --- NEW: Draw ECG Data ---
            cv2.putText(frame, f"ECG Raw: {last_ecg_data}", (10, 120), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 0), 2) 

            # --- 4. Show the one, combined frame ---
            cv2.imshow('Nexo Assistant and Car Control', frame)

    except Exception as e:
        print(f"[ERROR - OpenCV]: Could not initialize camera or load cascades: {e}")
        print("[System]: Main video loop FAILED to start. Assistant will run without it.")
        global_running_flag = False 
        return
    
    finally:
        if 'cap' in locals() and cap.isOpened():
            cap.release()
        cv2.destroyAllWindows()
        print("[System]: Main Video Loop Stopped.")
        
        print("[System]: Shutting down car...")
        if car_serial_port and car_serial_port.is_open:
            _send_command_to_serial("S") 
            car_serial_port.close()
            
        print("[System]: Shutting down ECG...")
        if ecg_serial_port and ecg_serial_port.is_open:
            ecg_serial_port.close()


# --- Voice Assistant Loop Function ---
def voice_assistant_loop():
    """
    Runs the main voice assistant logic (listen, think, speak) in a thread.
    """
    global global_running_flag, CHAT_HISTORY
    
    driver = None 
    
    try:
        while global_running_flag:
            user_input = listen()

            if not global_running_flag: 
                break

            if user_input:
                if any(phrase in user_input.lower() for phrase in ["goodbye nexo", "exit nexo", "shut down", "stop nexo"]):
                    response = "Understood. Shutting down now. Goodbye!"
                    speak(response)
                    global_running_flag = False
                    break
                    
                CHAT_HISTORY.append({"role": "user", "parts": [{"text": user_input}]})
                
                # --- THIS NOW CALLS THE ROUTER ---
                response = nexo_brain(CHAT_HISTORY, STRESS_LEVEL)
                
                if response:
                    if response.startswith("ACTION:"):
                        action_result = execute_pc_command(response, driver)
                        
                        if action_result == "EXIT":
                            global_running_flag = False 
                            driver = None 
                            break
                        else:
                            driver = action_result 
                            
                        CHAT_HISTORY.append({"role": "model", "parts": [{"text": response}]})
                        
                    else:
                        speak(response)
                        CHAT_HISTORY.append({"role": "model", "parts": [{"text": response}]})
                        save_chat_history()
                else:
                    speak("I'm sorry, I had trouble processing that. Could you try again?")
                    if CHAT_HISTORY:
                        CHAT_HISTORY.pop() 
            else:
                pass 
                
    except KeyboardInterrupt:
        print("\n[System]: Shutdown initiated by user (Ctrl+C).")
        speak("Shutting down. Goodbye!")
        global_running_flag = False
    
    finally:
        if driver:
            print("[System]: Voice assistant shutting down, closing browser...")
            driver.quit()
    
    print("[System]: Voice Assistant loop stopped.")


# --- MAIN EXECUTION (MODIFIED) ---
if __name__ == "__main__":
    
    # 1. Check if we are using Gemini and if the key is missing
    if not USE_OLLAMA and (not GEMINI_API_KEY or GEMINI_API_KEY == "YOUR_NEW_API_KEY_GOES_HERE"):
        print("="*50)
        print("ERROR: USE_OLLAMA is False, but GEMINI_API_KEY is not set.")
        print("Please add your Gemini key or set USE_OLLAMA = True")
        print("="*50)
    elif USE_OLLAMA:
         print("="*50)
         print(f"SYSTEM: Running in OLLAMA mode.")
         print(f"Connecting to: {OLLAMA_API_URL}")
         print(f"Using model: {OLLAMA_MODEL}")
         print(">>> Make sure your Ollama server is running! <<<")
         print("="*50)

    # 2. Check for WebDriver
    if not os.path.exists(DRIVER_PATH):
        print("="*50)
        print(f"ERROR: ChromeDriver not found at {DRIVER_PATH}")
        print("Please download chromedriver.exe and place it in the same folder.")
        print("Web automation features will be disabled.")
        print("="*50)
        
    # 3. Load chat history on startup
    CHAT_HISTORY = load_chat_history()

    # 4. Initial greeting
    print("[System]: Initializing Nexo...")
    speak("Hello! I am Nexo, your personal assistant. I am also initializing the car controller and stress monitor. How can I help you today?")
    time.sleep(2) 

    # 5. Start the voice assistant in a separate daemon thread
    print("[System]: Starting Voice Assistant Thread...")
    voice_thread = threading.Thread(target=voice_assistant_loop, daemon=True)
    voice_thread.start()
    
    # 6. Start the ECG Monitor Thread
    print("[System]: Starting ECG Monitor Thread...")
    if init_ecg_serial(): 
        ecg_thread = threading.Thread(target=ecg_data_reader_thread, daemon=True)
        ecg_thread.start()
        print("[System]: ECG Monitor thread started.")
    else:
        print("[System]: Failed to start ECG Monitor. Continuing without it.")
    
    # 7. Run the STRESS and CAR video loop in the main thread
    main_video_and_car_loop()

    # Main thread has finished (video loop exited)
    print("[System]: Main thread finished. Nexo assistant shutting down.")
    global_running_flag = False 
    voice_thread.join(timeout=2)  
    print("[System]: Shutdown complete.") 