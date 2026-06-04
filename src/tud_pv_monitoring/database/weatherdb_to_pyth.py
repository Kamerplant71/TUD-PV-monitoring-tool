import mysql.connector
import datetime
import pandas as pd
import numpy as np
from scipy.interpolate import interp1d
from zoneinfo import ZoneInfo


def mysql_init():
    """This function connects to the MySQL database

    Returns:
        conn: The MySQL database connection
        cursor: The cursor of the MySQL database
    """
    try:
        conn = mysql.connector.connect(
            host="100.65.177.87",   #IP address of the MySQL server
            user="OPET",
            password="npjust",
            database="pvmonitoring",
            port=3306                   # default MySQL port
        )

        cursor = conn.cursor()
        print('Succesfully connected to mysql db')
        return conn, cursor
    except:
        print('Failed to connect to mysql db')

    
def download_weather_last24hours(days, conn, cursor):
    """ Retrieves the weather data from the past 24 hours.

    Args:
        days (int): should be 1, but can be changed so it isn't 24 hours
        conn (_type_): The connection to the MySQL database
        cursor (_type_): The connection to the MySQL database
    Returns:
        list: Return a list containing all measurements from the past 24 hours.
    """
    cursor.execute("SELECT * FROM weather WHERE RecTime > %s;", (datetime.datetime.now() - datetime.timedelta(days=days),))
    weather_data = cursor.fetchall()
    return weather_data

def weather_last(conn, cursor):
    """Gives the measurement containing weather_id and the weather_time

    Args:
        conn (_type_): The connection to the MySQL database
        cursor (_type_): The cursor for the MySQL database

    Returns:
        last (list): Last measurement containing the weather_id and the datetime.
    """
    cursor.execute("SELECT idWeather, RecTime FROM weather ORDER BY RecTime DESC LIMIT 1")
    last = cursor.fetchone()
    return last


def weather_all(startdate, conn, cursor):
    """ Filter for all the weather measurements since the startdate of the new database

    Args:
        startdate (_datetime_): Startdate of the database, example: datetime.date(2026, 5, 20)
        conn (_type_): The connection to the MySQL database
        cursor (_type_): The cursor for the MySQL database

    Returns:
        data (_list_): All the measurements since the start date
    """
    cursor.execute("SELECT * FROM weather WHERE RecTime > %s;", (pd.to_datetime(startdate),))
    data = cursor.fetchall()
    return data

    
def mysql_close(conn):
    conn.close()
    
# conn, cur = mysql_init()
# data=weather_last(conn, cur)
# print(data)
# mysql_close(conn)
    