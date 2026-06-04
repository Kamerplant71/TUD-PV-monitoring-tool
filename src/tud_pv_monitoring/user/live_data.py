from tud_pv_monitoring.database import init, db_close
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.animation import FuncAnimation
import zoneinfo
import datetime
import matplotlib

""" Running this document will give you a live graph of the voltage and current of specified module. 
Change the module_name to change which module get plotted.
"""
module_name = "My_solar_panel_1"

def last_measurement(conn, cur , module_name):
    cur.execute("SELECT * FROM pv_point WHERE module_name=%s ORDER BY date_time DESC LIMIT 1" , (module_name,))
    column = cur.fetchone()
    print(column)
    conn.commit()
    return column
    
conn, cur, mysql_conn, mysql_cur = init()



matplotlib.set_loglevel("warning")
fig, ax1 = plt.subplots()
ax2 = ax1.twinx()

# Format x-axis for clock time
ax1.xaxis.set_major_formatter(
    mdates.DateFormatter('%H:%M:%S', tz=zoneinfo.ZoneInfo('Europe/Amsterdam'))
)
fig.autofmt_xdate()

times = []
voltages = []
currents = []

voltage_line, = ax1.plot([], [], 'g-', label="Voltage")
current_line, = ax2.plot([], [], 'b-', label="Current")

ax1.set_xlabel("Time")
ax1.set_ylabel("Voltage (V)", color='g')
ax2.set_ylabel("Current (A)", color='b')

# Combined legend
lines = [voltage_line, current_line]
labels = [l.get_label() for l in lines]
ax1.legend(lines, labels, loc='upper left')

def update(frame):
    measurement = last_measurement(conn, cur, module_name)

    times.append(measurement[0])      # datetime object
    voltages.append(measurement[4])   # voltage
    currents.append(measurement[5])   # current

    voltage_line.set_data(times, voltages)
    current_line.set_data(times, currents)

    # Relim and autoscale only the Y-axes to keep views tight to the data
    ax1.relim()
    ax1.autoscale_view(scalex=False, scaley=True)
    ax2.relim()
    ax2.autoscale_view(scalex=False, scaley=True)

    # Set a scrolling x-axis window showing the last 60 seconds
    window_duration = datetime.timedelta(seconds=600)
    ax1.set_xlim(times[-1] - window_duration, times[-1])

    return voltage_line, current_line

anim = FuncAnimation(
    fig,
    update,
    interval=3000,
    cache_frame_data=False
)

plt.show()


db_close(conn, mysql_conn)