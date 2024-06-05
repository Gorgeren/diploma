import psycopg2
from psycopg2test.config import host, user, password, db_name, port
import time
import sys
import os
import random as r
from datetime import datetime, timedelta
from multiprocessing import Process

USER_TABLE = "users"
TRANSACTIONS_TABLE = 'transactions'
PROCESSES_COUNT = 1

def pull_the_base(connection, request, size, logfile=None):
    with connection.cursor() as cursor:
        st = time.time()
        cursor.execute(request)
        end = time.time()
        ans = cursor.fetchall()
        if logfile:
            logfile.write(f"{size} {end - st}\n")
    return ans

def change_the_base(connection, request, size, logfile=None):
    with connection.cursor() as cursor:
        st = time.time()
        cursor.execute(request)
        end = time.time()
        if logfile:
            logfile.write(f"{size} {end - st}\n")

def process_write(file_path, id, batchSize, outlogfile):
    connection = None
    try:
        connection = psycopg2.connect(
            host=host,
            user=user,
            password=password,
            database=db_name,
            port=port
        )
        connection.autocommit = True

        logfile = None
        if id == PROCESSES_COUNT // 2:
            logfile = outlogfile
            logfile = open(logfile, 'a')
            print(f"process's writing {id=}")
        
        with open(file_path) as file:
            i = 0
            VALUES = ''
            for transaction in file:
                transaction = transaction.strip()
                i += 1
                if VALUES:
                    VALUES += ', '
                VALUES += transaction
                if i % batchSize == 0:
                    request = f"INSERT INTO {TRANSACTIONS_TABLE} (transaction_date, transaction_amount, person_id) VALUES {VALUES};"
                    change_the_base(connection, request, i, logfile)
                    VALUES = ''
    except Exception as _ex:
        print("[INFO] Error while working with PostgreSQL", _ex)
    finally:
        if connection:
            connection.close()
        if logfile:
            logfile.close()

def date_query():
    start_date = datetime(1990, 1, 1)
    end_date = datetime(2024, 12, 31)
    delta = end_date - start_date
    random_days = r.randrange(delta.days)
    first = start_date + timedelta(days=r.randrange(delta.days))
    second = start_date + timedelta(days=r.randrange(delta.days))
    if first > second:
        first, second = second, first
    return f'SELECT * FROM {TRANSACTIONS_TABLE} WHERE transaction_date BETWEEN \'{first}\' AND \'{second}\';'

def amount_query():
    first = r.uniform(-1000, 1000000)
    second = r.uniform(first, 1000000)
    return f'SELECT * FROM {TRANSACTIONS_TABLE} WHERE transaction_amount BETWEEN {round(first, 2)} AND {round(second, 2)};'

def amount_query_with_order_by_date():
    return amount_query()[:-1] + " ORDER BY transaction_date;"

def amount_query_with_order_by_amount():
    return amount_query()[:-1] + " ORDER BY transaction_amount;"

def date_query_with_order_by_date():
    return date_query()[:-1] + " ORDER BY transaction_date;"

def date_query_with_order_by_amount():
    return date_query()[:-1] + " ORDER BY transaction_amount;"

def read_data(id, req, batch, outlogfile):
    connection = None
    try:
        connection = psycopg2.connect(
            host=host,
            user=user,
            password=password,
            database=db_name,
            port=port
        )
        connection.autocommit = True

        logfile = open(outlogfile, 'a')
        size = 0
        while True:
            size += 1
            pull_the_base(connection, req(), size, logfile)
    except Exception as _ex:
        print("[INFO] Error while working with PostgreSQL", _ex)
    finally:
        if connection:
            connection.close()
        if logfile:
            logfile.close()

def test_read(batch):
    connection = None
    try:
        connection = psycopg2.connect(
            host=host,
            user=user,
            password=password,
            database=db_name,
            port=port
        )
        connection.autocommit = True

        requests = [
            'SELECT COUNT(*) FROM transactions WHERE transaction_amount BETWEEN 734259.7750751266 AND 775596.4841196195;'
        ]
        size = 0

        req = r.choice(requests)
        pull_the_base(connection, req, size)
    except Exception as _ex:
        print("[INFO] Error while working with PostgreSQL", _ex)
    finally:
        if connection:
            connection.close()

if __name__ == "__main__":
    files = [
        'source/200transactions1_id',
        'source/200transactions2_id',
        'source/200transactions3_id',
        'source/200transactions4_id',
        'source/200transactions5_id',
        'source/200transactions6_id',
        'source/200transactions7_id',
        'source/200transactions8_id',
        'source/200transactions9_id',
        'source/200transactions10_id'
    ]

    requests = [
        date_query,
        amount_query,
        amount_query_with_order_by_amount,
        date_query_with_order_by_amount,
        amount_query_with_order_by_date,
        date_query_with_order_by_date
    ]

    process_write(files[0], 1, 10, None)

    select = 0
    for test in range(1):
        batch = 1000
        select_logfile = f'{PROCESSES_COUNT * 200}transactions_{PROCESSES_COUNT}select_while_{PROCESSES_COUNT}write_batch{batch}'
        if select:
            insert_logfile = f'{PROCESSES_COUNT * 200}transactions_{PROCESSES_COUNT}write_while_{PROCESSES_COUNT}select_batch{batch}.log'
        else:
            insert_logfile = f'{PROCESSES_COUNT * 200}transactions_{PROCESSES_COUNT}write_batch{batch}.log'
        processes = []

        for i in range(PROCESSES_COUNT):
            p = Process(target=process_write, args=(files[i], i, batch, insert_logfile))
            p.start()
            processes.append(p)
        tmp = []
        if select:
            for i in range(PROCESSES_COUNT):
                p = Process(target=read_data, args=(i, requests[i], batch, select_logfile + requests[i].__name__ + '.log'))
                p.start()
                tmp.append(p)
                
        for p in processes:
            p.join()
