import random as r
from datetime import datetime, timedelta, time
import sys
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed

female = []
male = []
male_surnames = []
female_surnames = []
middle_names = []

def _read_file(path):
    with open(path, 'r', encoding='utf-8') as file:
        return file.read().split('\n')

def _init():
    global female, male, male_surnames, female_surnames
    global middle_names
    female = _read_file('source/female_names.txt')
    male = _read_file('source/male_names.txt')
    male_surnames = _read_file('source/surnames.txt')
    middle_names = _read_file('source/middle_names.txt')
    middle_names = [middle.split(' ') for middle in middle_names]
    female_surnames = []
    for surname in male_surnames:
        if surname.endswith("ов"):
            female_surnames.append(surname + 'а')
        elif surname.endswith("ин"):
            female_surnames.append(surname + 'а')
        elif surname.endswith("ев"):
            female_surnames.append(surname + 'а')
        elif surname.endswith("ый"):
            tmp = surname[:-2]
            tmp = tmp + "ая"
            female_surnames.append(tmp)
        elif surname.endswith("ий"):
            tmp = surname[:-2]
            tmp = tmp + "ая"
            female_surnames.append(tmp)
        else:
            female_surnames.append(surname)

def generate_people(n: int, file_path):
    with open(file_path, 'w', encoding='utf-8') as f:
        for i in range(n):
            flag = r.randint(0, 1)
            balance = round(r.uniform(0, 10000000), 2)
            if flag:
                s = (i + 1, r.choice(male), r.choice(middle_names)[0], r.choice(male_surnames), flag, balance)
            else:
                s = (i + 1, r.choice(female), r.choice(middle_names)[1], r.choice(female_surnames), flag, balance)
            f.write(f"{s}\n")
            if (i + 1) % (n // 10) == 0:
                print(f"Generated {i + 1} people", file=sys.stderr)

def generate_transactions(n: int, max_user_id, file_path, shift=0):
    with open(file_path, 'w', encoding='utf-8') as f:
        for i in range(shift, n + shift):
            start_date = datetime(1990, 1, 1)
            end_date = datetime(2024, 12, 31)
            delta = end_date - start_date
            random_days = r.randrange(delta.days)
            random_date = start_date + timedelta(days=random_days)
            
            random_hour = r.randint(0, 23)
            random_minute = r.randint(0, 59)
            random_second = r.randint(0, 59)
            random_time = time(random_hour, random_minute, random_second)
            
            transaction_datetime = datetime.combine(random_date.date(), random_time)
            ftransaction_datatime = transaction_datetime.strftime('%Y-%m-%d %H:%M:%S')
            transaction_amount = round(r.uniform(1, 1000000), 2)
            person_id = r.randint(1, max_user_id)
            s = (i + 1,ftransaction_datatime,transaction_amount,person_id)
            f.write(f"{s}\n")
            if (i + 1) % (n // 10) == 0:
                print(f"Generated {i + 1} transactions", file=sys.stderr)

_init()
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Generate users and transactions.')
    parser.add_argument('-files', type=int, default=5, help='Number of transaction files to generate (default: 5)')
    args = parser.parse_args()
    
    users = 5000
    transactions_batch = 20000
    transactions_files = args.files
    
    with ProcessPoolExecutor() as executor:
        futures = []
        futures.append(executor.submit(generate_people, users, 'source/5users_id.txt'))
        
        for number in range(1, transactions_files + 1):
            futures.append(executor.submit(generate_transactions, transactions_batch, users, f'source/200transactions{number}_id.txt'))
        
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as exc:
                print(f'Generated raised an exception: {exc}', file=sys.stderr)
