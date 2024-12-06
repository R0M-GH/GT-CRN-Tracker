import os
import requests
from dotenv import load_dotenv
from typing import Literal
import asyncio
from aiohttp import ClientSession
import sqlite3
from datetime import datetime
from data import fetch_course_data

load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
REG_LINK = os.getenv('REG_LINK')
TERM = os.getenv('TERM')
DB_NAME = 'data.db'
BOT_SEND_URL = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
COMMANDS = [
	{'command': 'help', 'description': 'Show all commands\' descriptions'},
	{'command': 'list', 'description': 'List all class CRNs being tracked with details like ID, section, CRN, and seat availability'},
	{'command': 'add', 'description': 'Add CRN(s) to the tracker. (e.g., /add 12345, 67890)'},
	{'command': 'rem', 'description': 'Remove CRN(s) from the tracker. (e.g., /rem 12345, 67890)'}
]
requests.post(f'https://api.telegram.org/bot{BOT_TOKEN}/setMyCommands', json={'commands': COMMANDS})
CRN_STATE = {}

try:
	connection = sqlite3.connect(DB_NAME)
	c = connection.cursor()
	c.execute('''CREATE TABLE IF NOT EXISTS user_data (chat_id INTEGER PRIMARY KEY, crns TEXT)''')
	connection.commit()
	connection.close()
except sqlite3.Error as e:
	print(f'\nError while creating DB: {e}')

def get_user_data(chat_id):
	try:
		connection = sqlite3.connect(DB_NAME)
		c = connection.cursor()
		c.execute('SELECT crns FROM user_data WHERE chat_id = ?', (chat_id,))
		result = c.fetchone()
		connection.close()
		if result and result[0]: return result[0].split(',')
		return []
	except sqlite3.Error as e:
		print(f'\nError while fetching user data: {e}')
		return []

def update_user_data(chat_id, crns):
	try:
		connection = sqlite3.connect(DB_NAME)
		c = connection.cursor()
		crns_str = ','.join(crns) if crns else ''
		c.execute('REPLACE INTO user_data (chat_id, crns) VALUES (?, ?)', (chat_id, crns_str))
		connection.commit()
		connection.close()
	except sqlite3.Error as e: print(f'\nError while updating user data: {e}')

async def course_check():
	while True:
		connection = sqlite3.connect(DB_NAME)
		c = connection.cursor()
		c.execute('SELECT chat_id FROM user_data')
		user_ids = [row[0] for row in c.fetchall()]
		connection.close()

		for chat_id in user_ids:
			await generate_course_info_and_notifs(chat_id, TERM)
		await asyncio.sleep(2)

async def generate_course_info_and_notifs(chat_id, term):
	async with ClientSession() as session:
		crns = get_user_data(chat_id)
		tasks = [fetch_course_data(session, term, crn) for crn in crns]
		results = await asyncio.gather(*tasks)
		for crn, name, data in results:
			if not name or not data: continue
			if int(data[2]) > 0: send_course_notification(chat_id, f'{name[2]} ({name[3]})', crn, 'open')
			if int(data[5]) > 0: send_course_notification(chat_id, f'{name[2]} ({name[3]})', crn, 'waitlist')

def send_course_notification(chat_id, course_str, crn, notif: Literal['waitlist', 'open']):
	msg = f'{notif.upper()} SEAT AVAILABLE: {course_str}\n\n{REG_LINK}'
	keyboard = {'inline_keyboard': [[{'text': f'Remove {crn}', 'callback_data': f'remove_{crn}'}]]}
	url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
	try:
		requests.post(BOT_SEND_URL, json={'chat_id': chat_id, 'text': msg, 'reply_markup': keyboard})
		requests.post(BOT_SEND_URL, json={'chat_id': chat_id, 'text': crn})
	except requests.exceptions.RequestException as e:
		print(f'\nError sending course notification for CRN {crn}: {e}')

def send_user_message(chat_id, msg, parse_mode=None):
	try:
		if parse_mode: requests.post(BOT_SEND_URL, json={'chat_id': chat_id, 'text': msg, 'parse_mode': parse_mode})
		else: requests.post(BOT_SEND_URL, json={'chat_id': chat_id, 'text': msg})
	except requests.exceptions.RequestException as e:
		print(f'\nError sending message to {chat_id}: {e}')

def send_user_keyboard(chat_id, msg, keyboard):
	try:
		requests.post(BOT_SEND_URL, json={'chat_id': chat_id, 'text': msg, 'reply_markup': keyboard})
	except requests.exceptions.RequestException as e:
		print(f'\nError sending message to {chat_id}: {e}')

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

							if text.startswith('/help'):
								try:
									help_message = "*Available commands:*\n\n"
									for command in COMMANDS: help_message += f'â¢ /*{command["command"]}* - {command["description"]}\n\n'
									send_user_message(chat_id, help_message, parse_mode='Markdown')
								except Exception as e:
									send_user_message(chat_id, "An error occurred while generating the help message.")

							elif text.startswith('/list'):
								if not crns:
									send_user_message(chat_id, 'No CRNs are being tracked.')
								else:
									async with ClientSession() as session:
										results = await asyncio.gather(*[fetch_course_data(session, TERM, crn) for crn in crns])

										course_list = []
										for crn, name, data in results:
											if name and data:
												course_list.append({'text': f'{name[2]} ({name[3]}) - {crn}', 'callback_data': f'course_{crn}'})
											else:
												course_list.append({'text': f'CRN {crn} (Invalid or Unavailable)', 'callback_data': f'course_{crn}'})

										if course_list:
											keyboard = {'inline_keyboard': [[item] for item in course_list]}
											send_user_keyboard(chat_id, 'Click on a course to view details:', keyboard)
										else:
											send_user_message(chat_id, 'No valid course details found for the tracked CRNs.')

							elif text.startswith('/add'):
								try:
									_, crns_to_add = text.split(maxsplit=1)
									add_crns = [crn.strip() for crn in crns_to_add.split(',')]
									for crn in add_crns:
										if crn not in crns:
											crns.append(crn)
											update_user_data(chat_id, crns)
											send_user_message(chat_id, f'Added CRN {crn} to tracking.')
										else:
											send_user_message(chat_id, f'CRN {crn} is already being tracked.')
								except ValueError:
									send_user_message(chat_id, 'Invalid format. Use /add {CRNs}. If adding multiple CRNs, separate them with commas.')
								
							elif text.startswith('/rem'):
								try:
									_, crns_to_remove = text.split(maxsplit=1)
									rem_crns = [crn.strip() for crn in crns_to_remove.split(',')]
									for crn in rem_crns: 
										if crn in crns:
											crns.remove(crn)
											update_user_data(chat_id, crns)
											send_user_message(chat_id, f'Removed CRN {crn} from tracking.')
										else:
											send_user_message(chat_id, f'CRN {crn} is not being tracked.')
								except ValueError:
									send_user_message(chat_id, 'Invalid format. Use /rem {CRNs}. If removing multiple CRNs, separate them with commas.')

						elif 'callback_query' in update:
							callback_query = update['callback_query']
							chat_id = callback_query['message']['chat']['id']
							callback_data = callback_query['data']

							if callback_data.startswith('course_'):
								crn = callback_data.split('_')[1]
								course_details = None
								async with ClientSession() as session: course_details = await fetch_course_data(session, TERM, crn)
								  
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
									send_user_message(chat_id, f'CRN {crn} removed from tracking.')
								else:
									send_user_message(chat_id, f'CRN {crn} is not being tracked.')

							elif callback_data == 'back_to_list':
								async with ClientSession() as session:
									results = await asyncio.gather(*[fetch_course_data(session, TERM, crn) for crn in crns])

									course_list = []
									for crn, name, data in results:
										if name and data:
											course_list.append({'text': f'{name[2]} ({name[3]}) - {crn}', 'callback_data': f'course_{crn}'})
										else:
											course_list.append({'text': f'CRN {crn} (Invalid or Unavailable)', 'callback_data': f'course_{crn}'})

									if course_list:
										keyboard = {'inline_keyboard': [[item] for item in course_list]}
										send_user_keyboard(chat_id, 'Click on a course to view details:', keyboard)
									else:
										send_user_message(chat_id, 'No valid course details found for the tracked CRNs.')

			current_time = datetime.now()
			connection = sqlite3.connect(DB_NAME)
			cursor = connection.cursor()
			cursor.execute('SELECT COUNT(*) FROM user_data')
			user_count = cursor.fetchone()[0]
			connection.close()

			refresh_time = asyncio.get_event_loop().time()
			print(f'\rUsers: \033[92m{user_count}\033[0m | Uptime: \033[91m{str(current_time - start_time).split(".")[0]}\033[0m | ' \
				  f'Refresh: \033[91m{str((current_time - last_refresh).total_seconds()).split(".")[0]}s\033[0m', end='')
			last_refresh = datetime.now()

			await asyncio.sleep(3)
		except Exception as e:
			print(f'\nError in telegram_handler loop: {e}')
			await asyncio.sleep(3)

if __name__ == '__main__':
	loop = asyncio.get_event_loop()
	loop.create_task(course_check())
	loop.run_until_complete(telegram_handler())
