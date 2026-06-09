import psycopg2
from psycopg2 import sql
import datetime
import zoneinfo
import time
import json
from pathlib import Path
import numpy as np
import pandas as pd
import ast
import pgvector.psycopg2
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from tud_pv_monitoring.database.weatherdb_to_pyth import download_weather_last24hours, mysql_init, mysql_close, weather_last, weather_all
import logging

logger = logging.getLogger(__name__)

def init():
    """This program sets the entire program up. 
    It opens the config file, it loads the datapath and established the connection with the postgreSQL db and the MySQL db.

    Returns:
        conn (_type_): The connection to the PostgreSQL database.
        cur (_type_): The cursor for the PostgreSQL database.
        mysql_conn (_type_): The connection to the MySQL database.
        mysql_cur (_type_): The cursor for the MySQL database.
        config (dict): The measurement config.
        data_path_base (_type_): The base location of the files. 
    """
    
    config, data_path_base = loadconfig()
    # Make connection with the PostgreSQL database.
    DB_NAME = "postgres"
    DB_USER = "postgress" # or remote 
    DB_PASS = "1234" # or npjust
    DB_HOST = "localhost" # or correct IP
    DB_PORT = "5432"
    try:
        conn = psycopg2.connect(database=DB_NAME,
                                user =DB_USER,
                                password=DB_PASS,
                                host=DB_HOST,
                                port=DB_PORT)
        print("Database connected succefully")
        logger.debug(f"Database connected succesfully")
    except Exception as e:
        print("Database not connected succesfully")
        logger.error(f"Database not connected succesfully. Error: {e}")
    cur = conn.cursor()
    
    # Make connection to the MySQL database.
    try:
        mysql_conn, mysql_cur = mysql_init()
        logger.debug(f"Weather database succesfully connected")
    except Exception as e:
        print('Weather database not connected succesfully')
        logger.error(f"Weather database not connected succesfully. Error: {e}")
        mysql_conn = None
        mysql_cur = None

    # Create pgvector extension if it doesn't exist
    try:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        conn.commit()
        print("pgvector extension loaded")
    except Exception as e:
        print(f"Could not create pgvector extension: {e}")
        print("Make sure pgvector is installed in PostgreSQL")
        
        
    return conn, cur, mysql_conn, mysql_cur

def loadconfig():
        # Open the documents with all the instructions.
    with open('opet-supervisor-config.json') as f:
        opet_supervisor_config = json.load(f)
    data_path_base = Path(opet_supervisor_config['data_path_base'])
    config_path = Path(opet_supervisor_config['config_path'])
    log_path_base = Path(opet_supervisor_config['log_path_base'])
    with open(config_path / 'measurement_config.json') as f:
        config = json.load(f)

    log_path_base.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(__name__)
    logging.basicConfig(
    filename=(log_path_base / 'opet-supervisor.log'),
    encoding='utf-8',
    level=logging.DEBUG,
    format='%(asctime)s %(message)s'
)
    return config, data_path_base

def update_loop():
    """ This loop adds newly collected data to the database every 10s."""
    conn, cur, mysql_conn, mysql_cur= init()
    while(1):
        config, data_path_base = loadconfig()
        date = str(datetime.date.today())
        try:
            add_data(date, conn, cur ,mysql_conn , mysql_cur, config, data_path_base)
            count_entries("pv_point", conn, cur)
            count_entries('pv_curve', conn, cur)
        except Exception as e: 
            print(f"Data could not be added, maybe the file is not yet created or there is an error: {e}")
            logger.error(f"Data could not be added, maybe the file is not yet created or there is an error: {e}")
            conn.rollback()
        
        try:
            add_weather_data(download_weather_last24hours(1, mysql_conn, mysql_cur), conn, cur)
            logger.debug("Weather data succesfully added")
        except Exception as e:
            print(f"Weather data could not be added. Error: {e}")
            logger.error(f"Weather data could not be added. Error {e}")
            conn.rollback()

        try:
            update_weather_id(date, conn, cur)
            logger.debug(f"Succesfully updated the weather_id")
        except Exception as e:
            logger.error(f"Weather_id could not be updated. Error {e}")
        time.sleep(10) # Wait for an 10s and check again.
    db_close(conn)


def daily_loop():
    """ This loop uploads past data at midnight so that data that was missed still gets uploaded.
        It also checks for errors in the system.
    """
    conn, cur, mysql_conn, mysql_cur= init()
    while(1):
        config, data_path_base = loadconfig()
        if datetime.datetime.now().hour == 0: #At midnight
            try:
                past_data_upload(conn, cur, mysql_conn, mysql_cur, config, data_path_base)
            except Exception as e: 
                print("Data could not be added, maybe the file is not yet created or there is an error")
                logging.error(f"Past data could not be added. Error: {e}")
                conn.rollback()
            
            try:
                error_detect(conn, cur, config)
            except Exception as e:
                print(f"Error detection failed with error: {e}")
                logging.error(f"Error detection failed with error: {e}")
                conn.rollback()
        time.sleep(60*60*1) # Wait for an hour and check again.
    db_close(conn)    
    
       
def past_data_upload(conn, cur, mysql_conn, mysql_cur, config, data_path_base):
    """ This function adds all the data to the database from a lookback number of days back.

    Args:
        conn (_type_): The connection to the PostgreSQL database.
        cur (_type_): The cursor for the PostgreSQL database.
        mysql_conn (_type_): The connection to the MySQL database.
        mysql_cur (_type_): The cursor for the MySQL database.
        config (dict): The measurement config.
        data_path_base (_type_): The base location of the files. 
    """
        
    start_date = datetime.date.today()-datetime.timedelta(days=config.get('lookback', 7))
    try:    
        add_weather_data(weather_all(start_date, mysql_conn, mysql_cur), conn, cur)
        logger.debug("Past weather data succesfully uploaded")
    except Exception as e:
        print('Could not add the weather data')
        logger.error(f"Weather data could not be added. Error: {e}")
        conn.rollback()
    
    for date in (start_date + datetime.timedelta(days=n) for n in range((datetime.date.today() - start_date + datetime.timedelta(days=1)).days)):
        try:
            add_data(str(date), conn, cur, mysql_conn, mysql_cur , config, data_path_base)
        except Exception as e: 
            print("Data could not be added, maybe the file is not yet created or there is an error")
            logger.error(f"Date could not be added on {date}. Error: {e}")
            conn.rollback()
        try:
            update_weather_id(str(date), conn, cur)
            logger.debug(f"weather_id succesfully updated")
        except Exception as e:
            logger.error(f"Weather_id not succesfully updated on {date}. Error: {e}")
            print('weather_id not succesfully updated')
            conn.rollback()
             

def add_data(date, conn, cur, mysql_conn, mysql_cur, config, data_path_base):
    """ Adds the data on a specifc date to the database

    Args:
        date (_string_): The date for which the data has to be added, example: "2026-05-20"
        conn (_type_): The connection to the PostgreSQL database
        cur (_type_): The cursor for the PostgreSQL database
        mysql_conn (_type_): The connection to the MySQL database
        mysql_cur (_type_): The cursor for the MySQL database
        config (_dict_): The measurement config
        data_path_base (_type_): The base location of the files. 
    """
    print(date)
    
    # Every time new measurement data gets added first for a new module has to be checked.
    add_module_data(config, conn, cur)
    
    data_path = data_path_base / date / config['data_destination']
    
    #point data add
    data_file_path = (
        data_path / (
        'opet_results_'
        + 'point'
        + '_' + date
        + '.csv'
        )
    )
    df = pd.read_csv(data_file_path, delimiter=",")
    
    # Add weather_id column with None values, this is done because the weather data is not always available, 
    # so the weather_id will be added later when the weather data is available, .
    # By adding the column with None values, the data can still be added to the database without having to worry about missing weather data.
    df['weather_id'] = None 
    df['date_time']=pd.to_datetime(df['date_time'])
    df['scheduled_time'] = pd.to_datetime(df['scheduled_time'])    
    
    # Insert the point data into the database
    data = df.to_numpy()
    point_insert = (
        "INSERT INTO pv_point (date_time, scheduled_time, module_name, mounted_on, v, i, status_integer, temperature_cell, axis_azimuth, axis_tilt, weather_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (date_time, module_name) DO NOTHING"
    )
    for d in data:
        cur.execute(point_insert, d)
    conn.commit()
    print('point data added')
    
    # Curve data add
    #Open document
    data_file_path = (
        data_path / (
        'opet_results_'
        + 'curve'
        + '_' + date
        + '.csv'
        )
    )
    df = pd.read_csv(data_file_path, delimiter=",")
    
    #From string to vector
    df["v"] = df["v"].apply(ast.literal_eval)
    df['i'] = df['i'].apply(ast.literal_eval)
    
    #Assigning weather_id None
    df['weather_id'] = None
    df['date_time']=pd.to_datetime(df['date_time'])
    df['scheduled_time'] = pd.to_datetime(df['scheduled_time'])    
    
    # Insert the curve data into the database
    data = df.to_numpy()
    curve_insert = (
        "INSERT INTO pv_curve (date_time, scheduled_time, measurement_duration, module_name, mounted_on, v, i, iv_status_integer, temperature_cell, axis_azimuth, axis_tilt, weather_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (date_time, module_name) DO NOTHING"
    )
    for d in data:
        cur.execute(curve_insert, d)
    conn.commit()
    print('curve data added')
 
    
def add_module_data(config, conn, cur):
    """ Adds the module data to the modules table

    Args:
        config (dict): The measurement config
        conn (_type_): The connection to the PostgreSQL database
        cur (_type_): The cursor for the PostgreSQL database
    """
    for module in config['modules']:
        module_insert = (
            "INSERT INTO modules (module_name, tracer, username, user_email, area, technology, manufacturer) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (module_name) DO NOTHING" # If there is already a module with the same name, the data will not be added.
        )
        query = "SELECT * FROM modules WHERE module_name = %s"
        cur.execute(query, (module['module_name'],))
        module_db = cur.fetchone()
        #print(module_db)
        conn.commit()
        try:
            if (module['module_name'] == module_db[0] and module.get('area') == module_db[4] and 
            module.get('technology') == module_db[5] and module.get('manufacturer') == module_db[6]):
                cur.execute("UPDATE modules SET tracer = %s, username= %s, user_email = %s", (module.get('tracer'), module.get('username'), module.get('user_email')))
                conn.commit()
                logger.debug(f"Updated the tracer, username and user_email")
            elif module['module_name'] == module_db[0]:
                #print('Module_name already exists')
                logger.debug('Duplicate module_name')
        except:
            logger.debug("Data could not be updated")
            logger.debug(f"Added a new module")
            cur.execute(module_insert, (module['module_name'], module.get('tracer'), module.get('username'), module.get('user_email'), module.get('area'), module.get('technology'), module.get('manufacturer')))
            conn.commit()


def add_weather_data(weather_data, conn, cur):
    """ Add the weather data collected to the weather table

    Args:
        weather_data (list): The weather data is to be added to the database
        conn (_type_): The connection to the PostgreSQL database
        cur (_type_): The cursor for the PostgreSQL database
    """
    weather_insert = (
        "INSERT INTO weather (weather_id, weather_time, temperature_air, relative_humidity, dew_point, relative_pressure, wind_speed, wind_speed_std, wind_direction, wind_direction_std, irradiance) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (weather_id) DO NOTHING"
    )
    df = pd.DataFrame(weather_data, columns=['weather_id', 'weather_time', 'temperature_air', 'relative_humidity', 'dew_point', 'relative_pressure', 'wind_speed', 'wind_speed_std', 'wind_direction', 'wind_direction_std', 'irradiance', 'IrrDirect', 'IrrDiffused', 'ETotal', 'EDirect', 'EDiffused'])
    df = df.drop(['IrrDirect', 'IrrDiffused', 'ETotal', 'EDirect', 'EDiffused'], axis=1) # drop the columns that are not needed.
    df['weather_time'] = pd.to_datetime(df['weather_time']).dt.tz_localize('Europe/Amsterdam', ambiguous='NaT', nonexistent='NaT')
    df['weather_time'] = df['weather_time'].astype(object).where(df['weather_time'].notna(), None)  # When there is a glitch with the time. This should make it None.
    result = df.to_numpy()
    
    for d in result:
        cur.execute(weather_insert, d) # the first column is the weather_id which is automatically generated by the database and is not needed to be added.
    conn.commit()


def update_weather_id(date, conn, cur):
    """Assigns a weather_id to a opet measurement. It first checks for weather_id's that have not been filled in yet. 
        Then it checks whether the measurement is 5 minutes or longer ago. Then it will assign the best fitting weather_id.

    Args:
        date (string): String that contains a date. Example: '2026-05-20'.
        conn (_type_): The connection to the PostgreSQL database.
        cur (_type_): The cursor for the PostgreSQL database.
        mysql_conn (_type_): The connection to the MySQL database.
        mysql_cur (_type_): The cursor for the MySQL datbase.
    """
    #Syncing for the pv_point measurements
    query1_point = "SELECT date_time FROM pv_point WHERE date_time>%s AND date_time<%s AND weather_id IS NULL"
    naive_date = (datetime.datetime.strptime(date, "%Y-%m-%d"))
    aware_date= naive_date.replace(tzinfo=zoneinfo.ZoneInfo('Europe/Amsterdam'))
    aware_date2 = aware_date+datetime.timedelta(days=1)
    cur.execute(query1_point, (aware_date, aware_date2))
    datetimes = cur.fetchall()
    conn.commit()
    
    for time in datetimes:
        time = time[0]
        now = datetime.datetime.now(tz=zoneinfo.ZoneInfo('Europe/Amsterdam'))
        if now-time>datetime.timedelta(minutes = 5):
            weatherid = weather_sync(time, conn, cur)
            query2_point = "UPDATE pv_point SET weather_id = %s WHERE date_time=%s" #Use the local weatherdb for speed.
            cur.execute(query2_point, (weatherid, time))
            conn.commit()
            
    query1_curve = "SELECT date_time FROM pv_curve WHERE date_time>%s AND date_time<%s AND weather_id IS NULL"
    naive_date = (datetime.datetime.strptime(date, "%Y-%m-%d"))
    aware_date= naive_date.replace(tzinfo=zoneinfo.ZoneInfo('Europe/Amsterdam'))
    aware_date2 = aware_date+datetime.timedelta(days=1)
    cur.execute(query1_curve, (aware_date, aware_date2))
    datetimes = cur.fetchall()
    conn.commit()
    
    for time in datetimes:
        time = time[0]
        now = datetime.datetime.now(tz=zoneinfo.ZoneInfo('Europe/Amsterdam'))
        if now-time>datetime.timedelta(minutes = 5):
            weatherid = weather_sync(time, conn, cur)
            query2_curve = "UPDATE pv_curve SET weather_id = %s WHERE date_time=%s" #Use the local weatherdb for speed.
            cur.execute(query2_curve, (weatherid, time))
            conn.commit()


def weather_sync(opet_date_time, conn, cursor):
    """ This function returns a weather id that is within a 5 minute range of a OPET data measurement. 
        If no weather measurement is within that timespan a 0 is returned.

    Args:
        opet_date_time (_datetime_): The date and time of the OPET measurement for which the weather_id has to be found. This should be a timezone aware datetime in the timezone of Europe/Amsterdam.
        conn (_type_): The connection to the PostgreSQL database
        cursor (_type_): The cursor for the PostgreSQL database

    Returns:
        _int_: The weather_id that is within a 5 minute range. 0 if no weather measurement is within that timespan.
    """
    
    cursor.execute("SELECT weather_id, weather_time FROM weather ORDER BY ABS(EXTRACT(EPOCH FROM (weather_time - %s))) ASC limit 1", (opet_date_time,))
    data = cursor.fetchone()
    data = np.array(data)
    #print(data)
    data[1]=data[1].replace(tzinfo=zoneinfo.ZoneInfo('Europe/Amsterdam'))
    if abs(opet_date_time-data[1])  < datetime.timedelta(minutes = 5):
        weatherid=data[0]
    else:
        weatherid= 0 
        
    return weatherid
        
    
def count_entries(type, conn, cur):
    """ Count the entries in a specific table

    Args:
        type (string): The table type, example: 'pv_point'
        conn (_type_): The connection to the PostgreSQL database
        cur (_type_): The cursor for the PostgreSQL database
    """
    cur.execute("SELECT COUNT(*) FROM "+type)
    count = cur.fetchall()
    for i in count:
        print(i)
    conn.commit()


def print_table(type, conn, cur):
    """ Prints a specific table

    Args:
        type (string): The table type, example: 'pv_point'
        conn (_type_): The connection to the PostgreSQL database
        cur (_type_): The cursor for the PostgreSQL database
    """
    cur.execute("SELECT * FROM "+type)
    table_pv = cur.fetchall()
    for i in table_pv:
        print(i)
    conn.commit()
 
    
def create_table(type, conn, cur):
    """ Creates a table of a specific type
    
    Args:
        type (string): The table type, example: 'pv_point'
        conn (_type_): The connection to the PostgreSQL database
        cur (_type_): The cursor for the PostgreSQL database
    """   
    
    
    if type == "pv_curve":
        command = """CREATE TABLE pv_curve(
            date_time TIMESTAMP WITH TIME ZONE,
            scheduled_time TIMESTAMP WITH TIME ZONE,
            measurement_duration float,
            module_name VARCHAR(255),
            mounted_on VARCHAR(255),
            v VECTOR(100),
            i VECTOR(100),
            iv_status_integer INT,
            temperature_cell float,
            axis_azimuth float,
            axis_tilt float,
            weather_id int,
            constraint fk_module FOREIGN KEY (module_name) REFERENCES modules(module_name),
            UNIQUE (date_time, module_name))"""
            #  constraint fk_weather FOREIGN KEY (weather_id) REFERENCES pv_Weather(weather_id),
    if type == "pv_point":
        command = """CREATE TABLE pv_point(
            date_time TIMESTAMP WITH TIME ZONE,
            scheduled_time TIMESTAMP WITH TIME ZONE,
            module_name VARCHAR(255),
            mounted_on VARCHAR(255),
            v float,
            i float,
            status_integer INT,
            temperature_cell float,
            axis_azimuth float,
            axis_tilt float,
            weather_id int,
            constraint fk_module FOREIGN KEY (module_name) REFERENCES modules(module_name),
            UNIQUE (date_time, module_name))"""
            #   constraint for the module_name means that module_name must be present in the modules table to be able to add data.
    if type == "pv_curve_test":
        command = """CREATE TABLE pv_curve_test(
            date_time TIMESTAMP WITH TIME ZONE,
            scheduled_time TIMESTAMP WITH TIME ZONE,
            measurement_duration float,
            module_name VARCHAR(255),
            mounted_on VARCHAR(255),
            v VECTOR(100),
            i VECTOR(100),
            iv_status_integer INT,
            temperature_cell float,
            axis_azimuth float,
            axis_tilt float,
            weather_id int,
            constraint fk_module FOREIGN KEY (module_name) REFERENCES modules(module_name),
            UNIQUE (date_time, module_name))"""
    if type == "pv_point_test":
        command = """CREATE TABLE pv_point_test(
            date_time TIMESTAMP WITH TIME ZONE,
            scheduled_time TIMESTAMP WITH TIME ZONE,
            module_name VARCHAR(255),
            mounted_on VARCHAR(255),
            v float,
            i float,
            status_integer INT,
            temperature_cell float,
            axis_azimuth float,
            axis_tilt float,
            weather_id int,
            constraint fk_module FOREIGN KEY (module_name) REFERENCES modules(module_name),
            UNIQUE (date_time, module_name))"""
            #   constraint for the module_name means that module_name must be present in the modules table to be able to add data.
            #   constraint fk_weather FOREIGN KEY (weather_id) REFERENCES weather(weather_id), 
            #   have removed it, because if the weather key would be missing the data would not be added, and this is not desired, because the weather data is not always available.
            #   By removing the foreign key constraint, the data will still be added, even if the weather data is missing.
    if type == "weather":
        command = """CREATE TABLE weather(
            weather_id int PRIMARY KEY,
            weather_time TIMESTAMP WITH TIME ZONE UNIQUE,
            temperature_air float,
            relative_humidity float,
            dew_point float,
            relative_pressure float,
            wind_speed float,
            wind_speed_std float,
            wind_direction float,
            wind_direction_std float,
            irradiance float)"""
    if type == "modules":
        command = """CREATE TABLE modules(
            module_name varchar(255) PRIMARY KEY,
            tracer varchar(255),
            username varchar(255),
            user_email varchar(255),
            area float,
            technology varchar(255),
            manufacturer varchar(255))"""
    try:
        cur.execute(command)
        conn.commit()
        print('Table '+type+' succesfully created')
    except Exception as e:
        conn.rollback()
        print(f"Table already exists or error: {e}")
   
    
def delete_table(type, conn, cur):
    """ Deletes an entire table and its contents. This cannot be undone and the table must recreated from scratch

    Args:
        type (string): The table type, example: 'pv_point'
        conn (_type_): The connection to the PostgreSQL database
        cur (_type_): The cursor for the PostgreSQL database
    """
    try:
        cur.execute("DROP TABLE "+type+ " CASCADE")
        conn.commit()
        print("Table "+type+" succesfully deleted")
    except Exception as e:
        conn.rollback()
        print(f"Delete failed (table may not exist): {e}")


#File: ADDRESS LOCATION AND TYPE.   type: mearuements type.     datetime: put in datetime in "2024-12-20 16:00:50-07:00" to filter the moments.     module_names (array): only get the name of the modules.
def download_table(file, type, datetime1, datetime2, module_name, conn, cur):
    """This function gives the ability to download the data from the database with filters over time and over modules.

    Args:
        file (CSV): 'file_name.csv'
        type (string): Measurement type: 'pv_point' or 'pv_curve'.
        datetime1 (string): start datetime in string, example: "2024-12-20 16:00:50+02:00".
        datetime2 (string): end datetime in string, example: "2026-12-20 16:00:50+02:00".
        module_name (list): List of the modules selected.
        conn (_type_): Connection to the PostgreSQL database.
        cur (_type_): cursor for the PostgreSQL database.
    """

    dt1 = datetime.datetime.fromisoformat(datetime1)
    dt2 = datetime.datetime.fromisoformat(datetime2)
    query = sql.SQL("COPY (SELECT * FROM " +type+ " LEFT JOIN weather ON "+type+".weather_id = weather.weather_id WHERE scheduled_time > %s AND scheduled_time < %s ORDER BY date_time DESC) TO STDOUT WITH DELIMITER ',' CSV HEADER ")
    with open(file, 'w') as f:
        formatted_query = cur.mogrify(query, [dt1,dt2]).decode('utf-8')
        cur.copy_expert(formatted_query, f)
    df = pd.read_csv(file)
    df['scheduled_time'] = pd.to_datetime(df['scheduled_time'])
    df.drop('weather_id.1', axis=1, inplace=True) # Drop the double weahter_id column
    result = df.loc[(df['module_name'].isin(module_name))]
    print(result)
    result.to_csv(file, index=False)
  
  
def print_table_type(type, conn, cur):
    """ Prints the types of each column of a specific table.

    Args:
        type (string): The table type, example: 'pv_point
        conn (_type_): The connection to the PostgreSQL database
        cur (_type_): The cursor for the PostgreSQL database
    """
    
    cur.execute(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{type}'")
    columns = cur.fetchall()
    for i in columns:
        print(i)
    conn.commit()
    
    
def retrieve_vector(conn, cur):
    """ Retrieves the voltage vector of the pv_curve

    Args:
        conn (_type_): The connection to the PostgreSQL database
        cur (_type_): The cursor for the PostgreSQL database
    """
    cur.execute("SELECT v FROM pv_curve")
    vector = cur.fetchall()
    for i in vector:
        print(i)
    conn.commit()
    
    
def send_mail(error, config, receiver_email = ''):
    """ Send an email with a specific message to a specific person and to admins.

    Args:
        error (string): The message that you want to send
        config (dict): The measurement config to load the setup.
        receiver_email (str, optional): additional emailaddress to send an email to. Defaults to ''.
    """
    
    smpt_server = "smtp.gmail.com"
    port = 587
    sender_email = 'wessel.oosterkamp@gmail.com'
    password = 'puxp gwhx zrsa cczv'
    admin = config['admins_email']
    if receiver_email == '':
        logging.debug(f'No useremail to send to')
        users= receiver_email + ',' + admin
    elif '@' in receiver_email and '.' in receiver_email:
        users = receiver_email + ',' + admin
        logging.debug(f"Email address is correct")
    else:
        receiver_email = ''
        users = receiver_email + ',' + admin
        logging.error(f"Emailaddress is not an emailaddress.")
    message = MIMEMultipart()
    message['From'] = sender_email
    message['To'] = users 
    message['Subject'] = 'There is a problem with the PV monitoring system'
    body = 'Error: ' + str(error)
    message.attach(MIMEText(body, 'plain'))
    
    
    try:
        server = smtplib.SMTP(smpt_server, port)
        server.starttls()
        server.login(sender_email, password)
        server.send_message(message)
        print("Email sent successfully")
        server.quit()
        logging.debug(f"Email has been send")
        
    except Exception as e:
        print(f"Error sending email: {e}")
        logging.error(f"Error sending email : {e}")
     
        
def error_detect(conn, cur, config):
    """ This function detects error in the system such as: problems with the entire system and issues per module. 
        When it has detected a problem it sends an email.
    
    Args:
        conn (_type_): The connection to the PostgreSQL database
        cur (_type_): The cursor for the PostgreSQL database
        config (dict): The measurement config to load the setup.
    """
    point_measurements = True
    curve_measurements = True
    # Check if data is being collected, if not send an email to the admin.
    cur.execute("SELECT date_time FROM pv_point ORDER BY date_time DESC LIMIT 1")
    last_entry_point = cur.fetchone()[0]
    cur.execute("SELECT date_time FROM pv_curve ORDER BY date_time DESC LIMIT 1")
    last_entry_curve = cur.fetchone()[0]
    
    # Check if curve and/or point measurements are being received
    if ((last_entry_point < (datetime.datetime.now(zoneinfo.ZoneInfo("Europe/Amsterdam")) - datetime.timedelta(days=1))) and 
        (last_entry_curve < (datetime.datetime.now(zoneinfo.ZoneInfo("Europe/Amsterdam")) - datetime.timedelta(days=1)))):
        send_mail('The PV monitoring system has not received data from both point and curve measurements in the past 24 hours \n' 
                 +'Most recent data from point measurements: ' + str(last_entry_point) 
                 +'\nMost recent data from curve measurements: ' + str(last_entry_curve), config
                 )
        point_measurements = False
        curve_measurements = False       
    elif last_entry_point < (datetime.datetime.now(zoneinfo.ZoneInfo("Europe/Amsterdam")) - datetime.timedelta(days =1)):
        send_mail('The PV monitoring system database has not received data from point measurements in the past 24 hours \n'
                 +'Most recent data from point measurements: ' 
                 + str(last_entry_point), config
                 )
        point_measurements = False
    elif last_entry_curve < (datetime.datetime.now(zoneinfo.ZoneInfo("Europe/Amsterdam")) - datetime.timedelta(days=1)):
        send_mail('The PV monitoring system database has not received data from curve measurements in the past 24 hours.\n'
                 +'Most recent data from curve measurements: ' 
                 + str(last_entry_curve), config
                 )
        curve_measurements = False

    # If data is collected check whether the OPETs are suffering from errors, ie status_integer is not 1. 
    # If there are errors, send an email to the user of the OPET and the admin.
    # Send the number of errors in the last 24 hours and the total number of measurements in the last 24 hours.
    for module in config['modules']: 
        cur.execute("SELECT date_time, status_integer FROM pv_point WHERE date_time > %s AND module_name = %s ORDER BY date_time DESC", 
                    (str(datetime.datetime.now() - datetime.timedelta(days=1)),) + (module['module_name'],)
                    )
        last_24h = cur.fetchall()
        errorcount = 0
        for i in range(len(last_24h)):
            if last_24h[i][1] != 1:
                errorcount += 1
        if errorcount > 0: # if there is at least one error in the last 24 hours, send an email
            if module.get('disabled', False) == False:
                send_mail(module['module_name']+' Has had an error in the past 24 hours, please check the system. \n'
                         +str(errorcount)+' of '+str(len(last_24h))
                         +' measurements have had an error in the past 24 hours', config, module.get('user_email', '')
                         )
        
        # Check whether the gets collected from the individual modules
        if module.get('disabled', False) == False:
            cur.execute("SELECT date_time FROM pv_point WHERE module_name = %s ORDER BY date_time DESC LIMIT 1", (module['module_name'],))
            last_entry_point = cur.fetchone()[0]
            cur.execute("SELECT date_time FROM pv_curve WHERE module_name = %s ORDER BY date_time DESC LIMIT 1", (module['module_name'],))
            last_entry_curve = cur.fetchone()[0]
            if (last_entry_point < (datetime.datetime.now(zoneinfo.ZoneInfo("Europe/Amsterdam")) - datetime.timedelta(days=1)) 
                and last_entry_curve < (datetime.datetime.now(zoneinfo.ZoneInfo("Europe/Amsterdam")) - datetime.timedelta(days=1)) 
                and point_measurements == True and curve_measurements == True):
                send_mail(module['module_name']+' on tracer: '+module.get('tracer', 'unknown tracer')
                         + ' has not received data from both point and curve measurements in the past 24 hours \n' 
                         + 'Most recent data from point measurements: ' 
                         + str(last_entry_point)
                         + '\nMost recent data from curve measurements: ' 
                         + str(last_entry_curve), 
                         config, module.get('user_email', '')
                         )
            elif (last_entry_point < (datetime.datetime.now(zoneinfo.ZoneInfo("Europe/Amsterdam")) - datetime.timedelta(days=1)) and 
                  point_measurements == True):
                send_mail(module['module_name']+' on tracer: '+module.get('tracer', 'unknown tracer')
                         + ' has not received data from point measurements in the past 24 hours \nMost recent data from point measurements: ' 
                         + str(last_entry_point), 
                         config, module.get('user_email', '')
                         )
            elif (last_entry_curve < (datetime.datetime.now(zoneinfo.ZoneInfo("Europe/Amsterdam")) - datetime.timedelta(days=1)) and 
                  curve_measurements == True):
                send_mail(module['module_name']+' on tracer: '+module.get('tracer', 'unknown tracer') 
                         + ' has not received data from curve measurements in the past 24 hours\nMost recent data from curve measurements: ' 
                         + str(last_entry_curve), 
                         config, module.get('user_email', '')
                         )


def data_tester(conn, cur):
    curve_insert = (
            "INSERT INTO weather (weather_id, weather_time, temperature_air, relative_humidity, dew_point, relative_pressure, wind_speed, wind_speed_std, wind_direction, wind_direction_std, irradiance) "
            "VALUES (2, '2026-05-26T10:20:00.028240+02:00', 23, 53, 10, 4, 10, 3, 360, 35, 400) "
            "ON CONFLICT (weather_time) DO NOTHING"
            )
    cur.execute(curve_insert)
    conn.commit()
    
    curve_insert = (
            "INSERT INTO modules (module_name, tracer, username, user_email, area, technology, manufacturer) "
            "VALUES ('My_solar_panel_1', 'O001', 'Wessel_Oosterkamp', 'woostekamp@tudelft.nl', 1.7, 'Monocrystalline', 'Longli') "
            "ON CONFLICT (module_name) DO NOTHING"
            )
    cur.execute(curve_insert)
    conn.commit()
    
    curve_insert = (
            "INSERT INTO pv_point (date_time, scheduled_time, module_name, mounted_on, v, i, status_integer, axis_azimuth, axis_tilt, weather_id) "
            "VALUES ('2026-05-19T10:05:50.028240+02:00','2026-05-18T10:05:50+02:00','My_solar_panel_1','Egis-tracker',-0.000303534,8.00177e-07,1,%s,%s,1) "
            "ON CONFLICT (date_time, module_name) DO NOTHING"
            )
    cur.execute(curve_insert, (180, 30))
    conn.commit()


def db_close(conn, mysql_conn):
    """ Close the connection with the PostgreSQL database

    Args:
        conn (_type_): The connection to the PostgreSQL database
    """
    conn.close()
    try:
        mysql_close(mysql_conn)
    except:
        logger.error(f"Could not close mysql connection")



# conn, cur, mysql_conn, mysql_cur= init()
# config, data_path_base, = loadconfig()
# delete_table('weather', conn, cur)
# create_table('weather', conn, cur)
# delete_table('pv_point', conn, cur)
# create_table('pv_point', conn, cur)
# data_tester(conn, cur)
# download_table('test1.csv', 'pv_point', "2026-05-25 16:00:50-07:00", "2026-12-29 16:00:50-07:00", ["My_solar_panel_1"], conn, cur)
# add_module_data(config, conn, cur)
#add_data('2026-05-26', conn, cur, mysql_conn, mysql_cur, config, data_path_base)
#add_weather_data(download_weather_last24hours(1000, mysql_conn, mysql_cur), conn, cur)
# print_table('weather', conn, cur)
# error_detect(conn, cur, config)
# data=weather_sync(datetime.datetime(2024, 2, 28, 10, 40,50, tzinfo=zoneinfo.ZoneInfo('Europe/Amsterdam')), conn, cur)
# print(data)
# update_weather_id('2026-05-26', conn, cur)
# print_table('pv_point', conn, cur)
# past_data_upload(conn, cur ,mysql_conn, mysql_cur, config, data_path_base)
# print_table('pv_curve', conn, cur)
# update_weather_id('2026-05-26', conn, cur)
# print_table('pv_curve',conn, cur)
# db_close(conn, mysql_conn)


