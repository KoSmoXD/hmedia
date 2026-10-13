"""Trusted local operator utility for securely provisioning the owner account."""
import argparse
import getpass
import sys

from app import NAME_RE, db, hash_password


def main():
    parser = argparse.ArgumentParser(description='Mr H Media owner setup')
    parser.add_argument('command', choices=['setup-owner'])
    parser.add_argument('--username', default='Mr_H')
    args = parser.parse_args()
    if not NAME_RE.fullmatch(args.username):
        sys.exit('Username must contain 3–24 letters, numbers, or underscores.')

    with db() as c:
        existing = c.execute('SELECT id,role FROM users WHERE username=? COLLATE NOCASE', (args.username,)).fetchone()
    if existing:
        print(f'Account {args.username} already exists. This will reset its password and assign the OWNER role.')
        if input('Type PROMOTE to confirm takeover: ').strip() != 'PROMOTE':
            sys.exit('Cancelled. No changes made.')
    password = getpass.getpass('New owner password (8+ characters): ')
    confirmation = getpass.getpass('Confirm password: ')
    if password != confirmation or not 8 <= len(password) <= 128:
        sys.exit('Passwords do not match or are not 8–128 characters. No changes made.')
    encrypted = hash_password(password)
    with db() as c:
        if existing:
            c.execute('UPDATE users SET password_hash=?,role=? WHERE id=?', (encrypted, 'owner', existing['id']))
            c.execute('DELETE FROM sessions WHERE user_id=?', (existing['id'],))
        else:
            c.execute('INSERT INTO users (username,display_name,password_hash,role) VALUES (?,?,?,?)',
                      (args.username, args.username, encrypted, 'owner'))
    print('Owner account configured. Log in through the website.')


if __name__ == '__main__':
    main()
