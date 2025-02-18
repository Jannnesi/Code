#!/usr/bin/env python3
# filepath: /home/jannesi/Code/Buttoncamera.py
from gpiozero import LED, Button
from picamera2 import Picamera2
import cv2
import time
from time import sleep, time

# Define GPIO pins
LED_PIN = 17   # GPIO pin for LED
BUTTON_PIN = 22  # GPIO pin for microswitch

# Initialize components
led = LED(LED_PIN)
button = Button(BUTTON_PIN, pull_up=True, bounce_time=0.05)  # Uses internal pull-up resistor

# Initialize and configure Picamera2 for still images
picam2 = Picamera2()
config = picam2.create_still_configuration()
picam2.configure(config)
picam2.start_preview()
picam2.start()

# Allow camera settings to settle
sleep(2)

# Function to toggle LED and capture an image when the button is pressed
def toggle_led_and_capture():
    led.toggle()  # Toggle LED state (ON <-> OFF)
    print("Button Pressed! LED state:", "ON" if led.is_lit else "OFF")
    
    try:
        # Capture the image to a NumPy array (assumed to be in RGB)
        image = picam2.capture_array()
        # Convert image from RGB to BGR format for OpenCV
        image_bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        # Create a unique filename using the current timestamp
        filename = f"capture_{int(time())}.jpg"
        cv2.imwrite(filename, image_bgr)
        print(f"Image captured and saved as {filename}")
    except Exception as e:
        print("Error capturing image:", e)

# Attach the event to the button press
button.when_pressed = toggle_led_and_capture
button.when_released = led.off

print("Press the button to toggle the LED and capture an image.")
try:
    while True:
        sleep(0.1)  # Keep the script running
except KeyboardInterrupt:
    print("\nExiting program.")
    picam2.stop_preview()
    picam2.close()