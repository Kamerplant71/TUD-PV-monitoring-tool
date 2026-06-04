from tud_pv_monitoring.database import init, db_close
import datetime
import pandas as pd
from psycopg2 import sql

def download_table(file, type, datetime1, datetime2, module_name, conn, cur):
    """This function gives the ability to download the data from the database with filters over time and over modules.

    Args:
        file (csv): 'file_name.csv'
        type (string): Measurement type: 'pv_point' or 'pv_curve'.
        datetime1 (string): start datetime in string, example: "2024-12-20 16:00:50-07:00".
        datetime2 (string): end datetime in string, example: "2026-12-20 16:00:50-07:00".
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
    
conn, cur, mysql_conn, mysql_cur = init()
download_table('test1.csv', 'pv_point', "2026-05-20 16:00:50+02:00", "2026-12-20 16:00:50+02:00", ["My_solar_panel_1"], conn, cur)
db_close(conn, mysql_conn)

  