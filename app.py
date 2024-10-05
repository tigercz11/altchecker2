from flask import Flask, request, jsonify, render_template
import sqlite3
import requests
import json
import hashlib
import base64
from urllib.parse import quote
from time import sleep
import re
from unidecode import unidecode

app = Flask(__name__)

CLIENT_ID = 'id'
CLIENT_SECRET = 'id'

# Function to remove character from the database
def remove_character_from_database(character_name, realm):
    conn = sqlite3.connect('/home/tigercz11/blizzard_accounts.db')  # Use full path
    c = conn.cursor()
    c.execute('''DELETE FROM characters WHERE character_name = ? AND realm = ?''', (character_name, realm))
    conn.commit()
    conn.close()

# Database initialization
def initialize_database():
    conn = sqlite3.connect('/home/tigercz11/blizzard_accounts.db')  # Use full path
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS characters (
                 id INTEGER PRIMARY KEY,
                 account_id INTEGER,
                 character_name TEXT,
                 realm TEXT,
                 pet_data TEXT,
                 UNIQUE(character_name, realm)
                 )''')
    conn.commit()
    conn.close()

initialize_database()

# Function to get access token for Blizzard API
def get_access_token():
    url = 'https://us.battle.net/oauth/token'
    data = {
        'grant_type': 'client_credentials',
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
    }
    response = requests.post(url, data=data)
    if response.status_code == 200:
        return response.json()['access_token']
    else:
        return None

# Function to fetch pet data
def fetch_pet_data(region, realm, character_name, access_token, timeout=5, retries=3):
    character_name_encoded = quote(character_name)
    base_url = f'https://{region}.api.blizzard.com/profile/wow/character/{realm}/{character_name_encoded}/collections/pets'
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Battlenet-Namespace': 'profile-eu',
    }

    for attempt in range(retries):
        try:
            response = requests.get(base_url, headers=headers, timeout=timeout)
            response.raise_for_status()
            full_data = response.json()
            pet_data = full_data.get("pets", [])
            pet_data_json = json.dumps(pet_data)
            pets_data_bytes = pet_data_json.encode('utf-8')
            sha256_hash = hashlib.sha256()
            sha256_hash.update(pets_data_bytes)
            petsback = sha256_hash.digest()
            pets_data = base64.b64encode(petsback).decode('utf-8')
            return character_name, realm, pets_data
        except requests.exceptions.HTTPError as http_err:
            # Handle the 404 case specifically to remove the character
            if response.status_code == 404:
                # Remove the character from the database if the error is 404 (not found)
                remove_character_from_database(character_name, realm)
                return character_name, realm, 'error_typing'
            # For other HTTP errors, do not remove, just retry or return None
            return character_name, realm, None
        except requests.exceptions.RequestException:
            # Retry for connection issues or timeouts
            if attempt < retries - 1:
                sleep(1)
                continue
            else:
                return character_name, realm, None

# Function to insert or update character data into database
def insert_or_update_character_data(account_id, character_name, realm, pet_data):
    conn = sqlite3.connect('/home/tigercz11/blizzard_accounts.db')  # Use full path
    c = conn.cursor()

    # Check if the character already exists
    c.execute('''SELECT id FROM characters WHERE character_name = ? AND realm = ?''', (character_name, realm))
    existing_character = c.fetchone()

    # Insert or update the character data
    if existing_character:
        c.execute('''UPDATE characters SET account_id = ?, pet_data = ? WHERE id = ?''',
                  (account_id, pet_data, existing_character[0]))
    else:
        c.execute('''INSERT INTO characters (account_id, character_name, realm, pet_data) VALUES (?, ?, ?, ?)''',
                  (account_id, character_name, realm, pet_data))

    conn.commit()
    conn.close()

# Function to check if pet data already exists in the database
def check_existing_pet_data(pet_data):
    conn = sqlite3.connect('/home/tigercz11/blizzard_accounts.db')  # Use full path
    c = conn.cursor()
    c.execute('''SELECT account_id, pet_data FROM characters''')
    existing_data = c.fetchall()
    for account_id, stored_pet_data in existing_data:
        if stored_pet_data == pet_data:
            conn.close()
            return account_id
    conn.close()
    return None

# Function to fetch next available account ID
def fetch_next_account_id():
    conn = sqlite3.connect('/home/tigercz11/blizzard_accounts.db')  # Use full path
    c = conn.cursor()
    c.execute('''SELECT MAX(account_id) FROM characters''')
    max_account_id = c.fetchone()[0]
    conn.close()
    if max_account_id is None:
        return 1
    else:
        return max_account_id + 1

# Function to merge accounts
def merge_accounts(account_id_to_keep, account_id_to_merge):
    conn = sqlite3.connect('/home/tigercz11/blizzard_accounts.db')  # Use full path
    c = conn.cursor()
    c.execute('''UPDATE characters SET account_id = ? WHERE account_id = ?''', (account_id_to_keep, account_id_to_merge))
    conn.commit()
    conn.close()

# Route to handle the form submission
@app.route('/submit', methods=['POST'])
def submit_character():
    character_name = request.form.get('character_name').lower()
    realm = request.form.get('realm').replace(' ', '-').lower()
    region = 'eu'  # Assuming EU region

    access_token = get_access_token()
    if not access_token:
        return render_template('form.html', error='Failed to get access token. Please try again later.')

    character_name, realm, pet_data = fetch_pet_data(region, realm, character_name, access_token, timeout=5)

    if pet_data == 'error_typing':
        return render_template('form.html', error='There was a mistake in typing the character name or realm, or the character has disabled API access. The character has been removed from the database.')

    if pet_data:
        existing_account_id = check_existing_pet_data(pet_data)
        if existing_account_id:
            account_id_to_keep = existing_account_id
        else:
            account_id_to_keep = fetch_next_account_id()

        # Check if the character already belongs to another account
        conn = sqlite3.connect('/home/tigercz11/blizzard_accounts.db')
        c = conn.cursor()
        c.execute('''SELECT account_id FROM characters WHERE character_name = ? AND realm = ?''', (character_name, realm))
        result = c.fetchone()
        conn.close()

        if result:
            current_account_id = result[0]
            if current_account_id != account_id_to_keep:
                merge_accounts(account_id_to_keep, current_account_id)

        insert_or_update_character_data(account_id_to_keep, character_name, realm, pet_data)

        # Fetch all characters with the same account ID
        conn = sqlite3.connect('/home/tigercz11/blizzard_accounts.db')
        c = conn.cursor()
        c.execute('SELECT character_name, realm FROM characters WHERE account_id = ?', (account_id_to_keep,))
        characters = c.fetchall()
        conn.close()

        # Organize the data by account_id
        accounts = {account_id_to_keep: [{'character_name': char[0], 'realm': char[1]} for char in characters]}

        return render_template('form.html', accounts=accounts)
    else:
        return render_template('form.html', error='Failed to fetch character data. Please try again later.')

# Function to normalize names by removing special characters
def normalize_name(name):
    normalized_name = unidecode(name)
    normalized_name = re.sub(r'[^a-zA-Z0-9]', '', normalized_name).lower()
    return normalized_name


# Route to get name suggestions
@app.route('/suggest_names', methods=['GET'])
def suggest_names():
    query = request.args.get('query', '').lower()
    if not query:
        return jsonify([])

    normalized_query = normalize_name(query)
    conn = sqlite3.connect('/home/tigercz11/blizzard_accounts.db')  # Use full path
    c = conn.cursor()
    c.execute('SELECT character_name, realm FROM characters')
    all_names = c.fetchall()
    conn.close()

    suggestions = []
    for name, realm in all_names:
        if normalized_query in normalize_name(name):
            suggestions.append({'name': name, 'realm': realm})

    return jsonify(suggestions[:20])

# Route to display form
@app.route('/')
def form():
    return render_template('form.html', accounts={})

if __name__ == '__main__':
    app.run(debug=True)
