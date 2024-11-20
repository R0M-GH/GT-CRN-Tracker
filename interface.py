import sqlite3
import os
import requests
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
DB_NAME = 'data.db'
BOT_SEND_URL = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'

def clear_all_crns():
    try:
        connection = sqlite3.connect(DB_NAME)
        c = connection.cursor()
        c.execute("UPDATE user_data SET crns = ''")
        connection.commit()
        connection.close()
        print("Successfully cleared all CRN data for all users.")
    except sqlite3.Error as e:
        print(f"Error while clearing CRN data: {e}")

def send_global_message(message):
    try:
        connection = sqlite3.connect(DB_NAME)
        c = connection.cursor()
        c.execute("SELECT chat_id FROM user_data")
        users = c.fetchall()
        connection.close()

        for user in users: send_user_message(user[0], message)
        
        print(f"Message sent to {len(users)} users.")
    except sqlite3.Error as e:
        print(f"Error while sending message to all users: {e}")

def send_user_message(chat_id, msg, parse_mode=None):
    try: requests.post(BOT_SEND_URL, json={'chat_id': chat_id, 'text': msg, 'parse_mode': parse_mode})
    except requests.exceptions.RequestException as e: print(f'\nError sending message to {chat_id}: {e}')

try:
    connection = sqlite3.connect(DB_NAME)
    c = connection.cursor()
    c.execute('''DROP TABLE IF EXISTS user_prefs''')
    connection.commit()
    connection.close()
    print("Successfully dropped user_prefs table.")
except sqlite3.Error as e:
    print(f'\nError while creating DB: {e}')