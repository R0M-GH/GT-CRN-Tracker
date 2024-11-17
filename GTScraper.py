import requests, os, sys
from bs4 import BeautifulSoup
from datetime import datetime
from tabulate import tabulate
from dotenv import load_dotenv
from typing import Literal
import asyncio
from aiohttp import ClientSession
import threading

load_dotenv()
BENDER_TOKEN = os.getenv('TELEGRAM_BENDER_TOKEN')
BENDER_CHAT = os.getenv('TELEGRAM_BENDER_CHAT_ID')

ignore_open = ['29925']
ignore_waitlist = ['22093']
endpoint = 'https://oscar.gatech.edu/pls/bprod/bwckschd.p_disp_detail_sched?term_in=%s&crn_in=%s'
reg_link = 'https://registration.banner.gatech.edu/StudentRegistrationSsb/ssb/registration/registration'

crn_file_lock = threading.Lock()
rows_lock = threading.Lock()
rows = []

def read_crns_from_file():
    with crn_file_lock:
        with open('GT\\scrapecrns.txt', 'r') as file:
            return file.readlines()[0].strip().split(', ')

def write_crns_to_file(crns):
    with crn_file_lock:
        with open('GT\\scrapecrns.txt', 'w') as file:
            file.write(', '.join(crns))

try:
    crns = read_crns_from_file()
    with open('GT\\session.txt', 'r') as file:
        session = file.readlines()[0].strip().split(' ')
except Exception as e:
    print(f'ERROR: {str(e)}')

term = session[1] + {'Spring': '02', 'Summer': '05', 'Fall': '08'}[session[0]]
urls = {crn: endpoint % (term, crn) for crn in crns}

commands = [
    {'command': 'list', 'description': 'List all class CRNs being tracked'},
    {'command': 'rem', 'description': 'Remove a CRN from the tracker (e.g., /rem {CRN})'},
    {'command': 'add', 'description': 'Add a CRN to the tracker (e.g., /add {CRN})'},
]
url = f'https://api.telegram.org/bot{BENDER_TOKEN}/setMyCommands'
requests.post(url, json={'commands': commands})

async def fetch_course_data(session, crn, url):
    async with session.get(url) as response:
        text = await response.text()
        soup = BeautifulSoup(text, 'html.parser')
        name = soup.find('th', {'class': 'ddlabel'}).get_text(strip=True).split(' - ')
        table = soup.find('table', {'summary': 'This layout table is used to present the seating numbers.'})
        data = [cell.get_text(strip=True) for cell in table.find_all('td', {'class': 'dddefault'})]
        return crn, name, data

async def generate_course_rows():
    global rows
    async with ClientSession() as session:
        tasks = [fetch_course_data(session, crn, url) for crn, url in urls.items()]
        results = await asyncio.gather(*tasks)
        new_rows = []
        for crn, name, data in results:
            if int(data[2]) > 0 and crn not in ignore_open: send_course_notification(BENDER_CHAT, f'{name[2]} ({name[3]})', crn, 'open')
            if int(data[5]) > 0 and crn not in ignore_waitlist: send_course_notification(BENDER_CHAT, f'{name[2]} ({name[3]})', crn, 'waitlist')
            new_rows.append([name[2], name[3], crn, data[2], f'{int(data[0]) - int(data[1]) - int(data[2])}', data[5]])

        with rows_lock: rows = new_rows

def send_course_notification(chat_id, course_str, crn, notif: Literal['waitlist', 'open']):
    msg = f'{notif.upper()} SEAT AVAILABLE: {course_str}\n\n{reg_link}'
    url = f'https://api.telegram.org/bot{BENDER_TOKEN}/sendMessage'
    requests.post(url, json={'chat_id': chat_id, 'text': msg})
    requests.post(url, json={'chat_id': chat_id, 'text': crn})

def send_course_notification(chat_id, course_str, crn, notif: Literal['waitlist', 'open']):
    msg = f'{notif.upper()} SEAT AVAILABLE: {course_str}\n\n{reg_link}'
    keyboard = {'inline_keyboard': [[{'text': f'Remove {crn}', 'callback_data': f'remove_{crn}'}]]}
    url = f'https://api.telegram.org/bot{BENDER_TOKEN}/sendMessage'
    requests.post(url, json={'chat_id': chat_id, 'text': msg, 'reply_markup': keyboard})
    requests.post(url, json={'chat_id': chat_id, 'text': crn})

def send_telegram_message(chat_id, msg):
    url = f'https://api.telegram.org/bot{BENDER_TOKEN}/sendMessage'
    requests.post(url, json={'chat_id': chat_id, 'text': msg})

def send_telegram_keyboard(chat_id, msg, keyboard):
    url = f'https://api.telegram.org/bot{BENDER_TOKEN}/sendMessage'
    requests.post(url, json={'chat_id': chat_id, 'text': msg, 'reply_markup': keyboard})

async def telegram_handler():
    global rows
    async with ClientSession() as session:
        offset = None
        while True:
            url = f'https://api.telegram.org/bot{BENDER_TOKEN}/getUpdates'
            if offset: url += f'?offset={offset}'
            async with session.get(url) as response:
                updates = await response.json()
                for update in updates.get('result', []):
                    offset = update['update_id'] + 1

                    if 'message' in update:
                        message = update['message']
                        text = message.get('text', '')
                        chat_id = message['chat']['id']

                        if text.startswith('/list'):
                            with rows_lock: keyboard = {'inline_keyboard': [[{'text': f'{row[0]} ({row[1]}) - {row[2]}', 'callback_data': f'course_{row[2]}'}] for row in rows]}
                            send_telegram_keyboard(chat_id, 'Click on a class to view details:', keyboard)

                        elif text.startswith('/rem'):
                            try:
                                _, crn = text.split()
                                if crn in urls:
                                    del urls[crn]
                                    crns.remove(crn)
                                    write_crns_to_file(crns)
                                    msg = f'CRN {crn} removed from tracking.'
                                else: msg = f'CRN {crn} is not being tracked.'
                                send_telegram_message(chat_id, msg)
                            except ValueError: send_telegram_message(chat_id, 'Invalid format. Use /rem {CRN}.')

                        elif text.startswith('/add'):
                            try:
                                _, crn = text.split()
                                if crn not in urls:
                                    urls[crn] = endpoint % (term, crn)
                                    crns.append(crn)
                                    write_crns_to_file(crns)
                                    send_telegram_message(chat_id, f'CRN {crn} added to tracking.')
                                else: send_telegram_message(chat_id, f'CRN {crn} is already being tracked.')
                            except ValueError: send_telegram_message(chat_id, 'Invalid format. Use /add {CRN}.')

                    elif 'callback_query' in update:
                        callback_query = update['callback_query']
                        chat_id = callback_query['message']['chat']['id']
                        callback_data = callback_query['data']

                        if callback_data.startswith('course_'):
                            crn = callback_data.split('_')[1]
                            course_details = None
                            with rows_lock:
                                for row in rows:
                                    if row[2] == crn:
                                        course_details = row
                                        break
                            if course_details:
                                course_details_msg = f'Course: {course_details[0]} {course_details[1]}\n' \
                                                     f'CRN: {course_details[2]}\n' \
                                                     f'Remaining Seats: {course_details[3]}\n' \
                                                     f'Prospective Seats: {course_details[4]}\n' \
                                                     f'Waitlist: {course_details[5]}'
                                keyboard = {'inline_keyboard': [[{'text': 'Back to List', 'callback_data': 'back_to_list'}]]}
                                send_telegram_keyboard(chat_id, course_details_msg, keyboard)

                        elif callback_data.startswith('remove_'):
                            crn = callback_data.split('_')[1]
                            if crn in urls:
                                del urls[crn]
                                crns.remove(crn)
                                write_crns_to_file(crns)
                                send_telegram_message(chat_id, f'CRN {crn} removed from tracking.')
                            else: send_telegram_message(chat_id, f'CRN {crn} is not being tracked.')

                        elif callback_data == 'back_to_list':
                            with rows_lock:
                                keyboard = {'inline_keyboard': [[{'text': f'{row[0]} ({row[1]}) - {row[2]}', 'callback_data': f'course_{row[2]}'}] for row in rows]}
                            send_telegram_keyboard(chat_id, 'Click on a class to view details:', keyboard)

            await asyncio.sleep(3)

def colorize_table(rows):
    def color(condition): return '\033[92m' if condition else '\033[91m'
    return [[row[0], row[1], row[2],
            f'{color(int(row[3]) > 0)}{row[3]}\033[0m',
            f'{color(int(row[4]) > 0)}{row[4]}\033[0m',
            f'{color(int(row[5]) > 0)}{row[5]}\033[0m'] for row in rows]

async def periodic():
    while True:
        sys.stdout.write('\033[H\033[J')
        print('R0M\'s GT Course Tracker\n')
        print(datetime.now().strftime('\n%m/%d/%y %H:%M:%S.%f'))
        await generate_course_rows()
        with rows_lock: print(tabulate(colorize_table(rows), headers=['Course', 'Section', 'CRN', 'Remaining', 'Prospect', 'Waitlist'], tablefmt='grid'))
        await asyncio.sleep(10)

async def main():
    await asyncio.gather(telegram_handler(), periodic())

if __name__ == '__main__':
    asyncio.run(main())
