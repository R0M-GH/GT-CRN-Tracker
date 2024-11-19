import os
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from typing import Literal
import asyncio
from aiohttp import ClientSession
import sqlite3
import threading
from datetime import datetime, timedelta

load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')

ENDPOINT = 'https://oscar.gatech.edu/pls/bprod/bwckschd.p_disp_detail_sched?term_in=%s&crn_in=%s'
REG_LINK = 'https://registration.banner.gatech.edu/StudentRegistrationSsb/ssb/registration/registration'

DB_NAME = 'data.db'
term = '202502'
CRN_STATE = {}

try:
    connection = sqlite3.connect(DB_NAME)
    c = connection.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS user_data (chat_id INTEGER PRIMARY KEY, crns TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_prefs (chat_id INTEGER PRIMARY KEY, mute_all BOOLEAN, mute_waitlist BOOLEAN)''')
    connection.commit()
    connection.close()
except sqlite3.Error as e:
    print(f"Error while creating DB: {e}")

def get_user_data(chat_id):
    try:
        connection = sqlite3.connect(DB_NAME)
        c = connection.cursor()
        c.execute("SELECT crns FROM user_data WHERE chat_id = ?", (chat_id,))
        result = c.fetchone()
        connection.close()
        if result: return result[0].split(',')
        return []
    except sqlite3.Error as e:
        print(f"Error while fetching user data: {e}")
        return []

def update_user_data(chat_id, crns):
    try:
        connection = sqlite3.connect(DB_NAME)
        c = connection.cursor()
        crns_str = ','.join(crns)
        c.execute("REPLACE INTO user_data (chat_id, crns) VALUES (?, ?)", (chat_id, crns_str))
        connection.commit()
        connection.close()
    except sqlite3.Error as e: print(f"Error while updating user data: {e}")

def get_user_prefs(chat_id):
    try:
        connection = sqlite3.connect(DB_NAME)
        c = connection.cursor()
        c.execute("SELECT mute_all, mute_waitlist FROM user_prefs WHERE chat_id = ?", (chat_id,))
        result = c.fetchone()
        connection.close()
        if result: return {'mute_all': result[0], 'mute_waitlist': result[1]}
        return {'mute_all': False, 'mute_waitlist': False}
    except sqlite3.Error as e:
        print(f"Error while fetching user preferences: {e}")
        return {'mute_all': False, 'mute_waitlist': False}

def update_user_prefs(chat_id, mute_all, mute_waitlist):
    try:
        connection = sqlite3.connect(DB_NAME)
        c = connection.cursor()
        c.execute("REPLACE INTO user_prefs (chat_id, mute_all, mute_waitlist) VALUES (?, ?, ?)", (chat_id, mute_all, mute_waitlist))
        connection.commit()
        connection.close()
    except sqlite3.Error as e: print(f"Error while updating user preferences: {e}")

commands = [
    {'command': 'list', 'description': 'List all class CRNs being tracked'},
    {'command': 'add', 'description': 'Add a CRN to the tracker (e.g., /add {CRN})'},
    {'command': 'rem', 'description': 'Remove a CRN from the tracker (e.g., /rem {CRN})'}
]
requests.post(f'https://api.telegram.org/bot{BOT_TOKEN}/setMyCommands', json={'commands': commands})

async def fetch_course_data(session, term, crn):
    url = ENDPOINT % (term, crn)
    try:
        async with session.get(url) as response:
            text = await response.text()
            soup = BeautifulSoup(text, 'html.parser')
            try:
                name = soup.find('th', {'class': 'ddlabel'}).get_text(strip=True).split(' - ')
                table = soup.find('table', {'summary': 'This layout table is used to present the seating numbers.'})
                data = [cell.get_text(strip=True) for cell in table.find_all('td', {'class': 'dddefault'})]
                return crn, name, data
            except AttributeError:
                return crn, None, None
    except Exception as e:
        print(f"Error fetching course data for CRN {crn}: {e}")
        return crn, None, None

async def course_check():
    while True:
        connection = sqlite3.connect(DB_NAME)
        c = connection.cursor()
        c.execute("SELECT chat_id FROM user_data")
        user_ids = [row[0] for row in c.fetchall()]
        connection.close()

        for chat_id in user_ids:
            await generate_course_info_and_notifs(chat_id, term)
        await asyncio.sleep(5)


async def generate_course_info_and_notifs(chat_id, term):
    async with ClientSession() as session:
        crns = get_user_data(chat_id)
        tasks = [fetch_course_data(session, term, crn) for crn in crns]
        results = await asyncio.gather(*tasks)
        for crn, name, data in results:
            if not name or not data: continue
            prefs = get_user_prefs(chat_id)
            if int(data[2]) > 0 and not prefs['mute_all']: send_course_notification(chat_id, f'{name[2]} ({name[3]})', crn, 'open')
            if int(data[5]) > 0 and not prefs['mute_waitlist']: send_course_notification(chat_id, f'{name[2]} ({name[3]})', crn, 'waitlist')

def send_course_notification(chat_id, course_str, crn, notif: Literal['waitlist', 'open']):
    msg = f'{notif.upper()} SEAT AVAILABLE: {course_str}\n\n{REG_LINK}'
    keyboard = {'inline_keyboard': [[{'text': f'Remove {crn}', 'callback_data': f'remove_{crn}'}]]}
    url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
    try:
        requests.post(url, json={'chat_id': chat_id, 'text': msg, 'reply_markup': keyboard})
        requests.post(url, json={'chat_id': chat_id, 'text': crn})
    except requests.exceptions.RequestException as e:
        print(f"Error sending course notification for CRN {crn}: {e}")

def send_user_message(chat_id, msg):
    url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
    try:
        requests.post(url, json={'chat_id': chat_id, 'text': msg})
    except requests.exceptions.RequestException as e:
        print(f"Error sending message to {chat_id}: {e}")

def send_user_keyboard(chat_id, msg, keyboard):
    url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
    try:
        requests.post(url, json={'chat_id': chat_id, 'text': msg, 'reply_markup': keyboard})
    except requests.exceptions.RequestException as e:
        print(f"Error sending message to {chat_id}: {e}")

async def telegram_handler():
    offset = None
    start_time = datetime.now()
    last_refresh = start_time

    while True:
        url = f'https://api.telegram.org/bot{BOT_TOKEN}/getUpdates'
        if offset: url += f'?offset={offset}'
        try:
            async with ClientSession() as session:
                async with session.get(url) as response:
                    updates = await response.json()
                    for update in updates.get('result', []):
                        offset = update['update_id'] + 1

                        if 'message' in update:
                            message = update['message']
                            text = message.get('text', '')
                            chat_id = message['chat']['id']

                            crns = get_user_data(chat_id)
                            prefs = get_user_prefs(chat_id)

                            if text.startswith('/list'):
                                if not crns:
                                    send_user_message(chat_id, "No CRNs are being tracked.")
                                else:
                                    async with ClientSession() as session:
                                        results = await asyncio.gather(*[fetch_course_data(session, term, crn) for crn in crns])

                                        course_list = []
                                        for crn, name, data in results:
                                            if name and data:
                                                course_list.append({'text': f"{name[2]} ({name[3]}) - {crn}", 'callback_data': f'course_{crn}'})
                                            else:
                                                course_list.append({'text': f"CRN {crn} (Invalid or Unavailable)", 'callback_data': f'course_{crn}'})

                                        if course_list:
                                            keyboard = {'inline_keyboard': [[item] for item in course_list]}
                                            send_user_keyboard(chat_id, "Click on a course to view details:", keyboard)
                                        else:
                                            send_user_message(chat_id, "No valid course details found for the tracked CRNs.")


                            elif text.startswith('/add'):
                                try:
                                    _, crn = text.split()
                                    if crn not in crns:
                                        crns.append(crn)
                                        update_user_data(chat_id, crns)
                                        send_user_message(chat_id, f"Added CRN {crn} to tracking.")
                                    else:
                                        send_user_message(chat_id, f"CRN {crn} is already being tracked.")
                                except ValueError:
                                    send_user_message(chat_id, "Invalid format. Use /add {CRN}.")
                                
                            elif text.startswith('/rem'):
                                try:
                                    _, crn = text.split()
                                    if crn in crns:
                                        crns.remove(crn)
                                        update_user_data(chat_id, crns)
                                        send_user_message(chat_id, f"Removed CRN {crn} from tracking.")
                                    else:
                                        send_user_message(chat_id, f"CRN {crn} is not being tracked.")
                                except ValueError:
                                    send_user_message(chat_id, "Invalid format. Use /rem {CRN}.")
                        
                            elif text.startswith('/mute_all'):
                                update_user_prefs(chat_id, True, prefs['mute_waitlist'])
                                send_user_message(chat_id, "All notifications are now muted.")

                            elif text.startswith('/unmute_all'):
                                update_user_prefs(chat_id, False, prefs['mute_waitlist'])
                                send_user_message(chat_id, "All notifications are now unmuted.")
                        
                            elif text.startswith('/mute_waitlist'):
                                update_user_prefs(chat_id, prefs['mute_all'], True)
                                send_user_message(chat_id, "Waitlist notifications are now muted.")

                            elif text.startswith('/unmute_waitlist'):
                                update_user_prefs(chat_id, prefs['mute_all'], False)
                                send_user_message(chat_id, "Waitlist notifications are now unmuted.")

                        elif 'callback_query' in update:
                            callback_query = update['callback_query']
                            chat_id = callback_query['message']['chat']['id']
                            callback_data = callback_query['data']

                            if callback_data.startswith('course_'):
                                crn = callback_data.split('_')[1]
                                course_details = None
                                async with ClientSession() as session: course_details = await fetch_course_data(session, term, crn)
                                  
                                if course_details[1]:
                                    message = f'Course: {course_details[1][2]} ({course_details[1][3]}) - {course_details[1][0]}\n' \
                                              f'CRN: {course_details[0]}\nRemaining Seats: {course_details[2][2]}\nWaitlist: {course_details[2][5]}'
                                    keyboard = {'inline_keyboard': [[{'text': 'Back to List', 'callback_data': 'back_to_list'},
                                                                     {'text': 'Remove from List', 'callback_data': f'remove_{crn}'}]]}
                                    send_user_keyboard(chat_id, message, keyboard)

                            elif callback_data.startswith('remove_'):
                                crn = callback_data.split('_')[1]
                                if crn in crns:
                                    crns.remove(crn)
                                    update_user_data(chat_id, crns)
                                    send_user_message(chat_id, f"CRN {crn} removed from tracking.")
                                else:
                                    send_user_message(chat_id, f"CRN {crn} is not being tracked.")

                            elif callback_data == 'back_to_list':
                                if crns:
                                    keyboard = {'inline_keyboard': [[{'text': f'CRN: {crn}', 'callback_data': f'course_{crn}'}] for crn in crns]}
                                    send_user_keyboard(chat_id, "Click on a CRN to view details:", keyboard)
            
            current_time = datetime.now()
            connection = sqlite3.connect(DB_NAME)
            cursor = connection.cursor()
            cursor.execute("SELECT COUNT(*) FROM user_data")
            user_count = cursor.fetchone()[0]
            connection.close()

            refresh_time = asyncio.get_event_loop().time()
            print(f"\rUsers: \033[92m{user_count}\033[0m | Uptime: \033[91m{str(current_time - start_time).split('.')[0]}\033[0m | " \
                  f"Refresh: \033[91m{str((current_time - last_refresh).total_seconds()).split('.')[0]}s\033[0m", end="")
            last_refresh = datetime.now()

            await asyncio.sleep(3)
        except Exception as e:
            print(f"Error in telegram_handler loop: {e}")
            await asyncio.sleep(3)

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(course_check())
    loop.run_until_complete(telegram_handler())
