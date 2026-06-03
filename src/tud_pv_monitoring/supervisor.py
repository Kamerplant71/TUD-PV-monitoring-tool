import multiprocessing
import datetime
from tud_pv_monitoring.measurement_scheduling_tools import datetime_range, present, next_occurrence
import json
from tud_pv_monitoring.database import daily_loop, update_loop
from tud_pv_monitoring.measurements.opet_supervisor_tools import measurement_loop, writer_loop
import logging
import traceback
from zoneinfo import ZoneInfo
import sys
from pathlib import Path

# Constants
# Measurements more than this far into the future will be handled on a
# subsequent loop
MAXIMUM_WAIT = 0.1  # s
# Infinite loops with nothing to do will delay this much before checking again
MINIMUM_WAIT = 0.01  # s
# Measurements are scheduled only this far into the future
SCHEDULE_HORIZON = 32  # s
# The schedule is updated this often
SCHEDULE_INTERVAL = 30  # s
#Maximum time a job may exist
MAX_JOB_TIME = datetime.timedelta(minutes=5)

TZ_LOCAL = ZoneInfo("Europe/Amsterdam")

# Load from .json files
with open('opet-supervisor-config.json') as f:
    opet_supervisor_config = json.load(f)

CONFIG_PATH = Path(opet_supervisor_config['config_path'])
DATA_PATH_BASE = Path(opet_supervisor_config['data_path_base'])
LOG_PATH_BASE = Path(opet_supervisor_config['log_path_base'])

with open(CONFIG_PATH / 'opet_info.json') as f:
    load_info = json.load(f)

with open(CONFIG_PATH / 'opet_bus_info.json') as f:
    bus_info = json.load(f)

with open(CONFIG_PATH / 'measurement_config.json') as f:
    config = json.load(f)


# Setup logging
LOG_PATH_BASE.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger(__name__)
logging.basicConfig(
    filename = (
        LOG_PATH_BASE 
        / f'opet-supervisor-{datetime.date.today().isoformat()}.log'
    ),
    encoding = 'utf-8',
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s'
)

logger.debug('opet-supervisor started')

# Set up a handler to log uncaught exceptions
def exception_handler(exctype, value, tb):
    logger.exception(''.join(traceback.format_exception(exctype, value, tb)))

sys.excepthook = exception_handler


if __name__ == '__main__':
    with multiprocessing.Manager() as manager:
        # Job definitions will be values in this shared dictionary.
        # Keys are unique job IDs so they can be removed from the dictionary
        # even if the dictionary gets updated while a job is underway. The
        # Manager doesn't handle updates to *nested* dictionaries for other
        # processes, so this code tends to pop things out of dictionaries,
        # work with them, then reinsert them if needed
        jobs = manager.dict({})
        results = manager.dict({})
        jobs_in_progress = manager.dict({})
        job_id = 0

        shutdown_event = manager.Event()
        # All possible buses. Each bus will get a process
        buses_all = {
            load_info_datum["bus"]
            for load_info_datum in load_info.values()
        }

        # Create a process for each bus
        processes = [
            multiprocessing.Process(
                target=measurement_loop,
                args=(
                    bus, 
                    jobs,
                    jobs_in_progress,
                    results, 
                    bus_info, 
                    load_info, 
                    MAXIMUM_WAIT, 
                    MINIMUM_WAIT, 
                    shutdown_event,
                    MAX_JOB_TIME
                ),
                name=f'measurement-bus-{bus}',
            )
            for bus
            in buses_all
        ]

        # Add one to do the logging
        processes.append(
            multiprocessing.Process(
                target=writer_loop,
                args=(
                    results,
                    DATA_PATH_BASE, 
                    TZ_LOCAL, 
                    MINIMUM_WAIT,
                    shutdown_event
                    ),
                name='writer',
            )
        )

        # Process for daily loop
        processes.append(
            multiprocessing.Process(
                target=daily_loop,
                name='daily_loop'
            )
        )       

        # Process for update loop database
        processes.append(
            multiprocessing.Process(
                target=update_loop,
                name='update_loop'
            )
        )        

        for process in processes:
            process.start()

        schedule_update_time = next_occurrence(
            present(),
            datetime.timedelta(seconds=SCHEDULE_INTERVAL)
        )
        schedule_never_updated = True
        try:
            while not shutdown_event.is_set():
                if schedule_never_updated:
                    schedule_never_updated = False
                else:
                    while present() < schedule_update_time and not shutdown_event.is_set():
                        shutdown_event.wait(MINIMUM_WAIT)
                    schedule_update_time += datetime.timedelta(seconds=SCHEDULE_INTERVAL)

                # Check if processes are still active and restart inactive ones
                for i, process in enumerate(processes):
                    if shutdown_event.is_set():
                        break
                    
                    if process.is_alive():
                        continue
                    
                    logger.error(f'Process {process.name} died: restarting')
                    process.join(timeout=1)

                    old_name = process.name
                    

                    # Make new process based on name
                    if old_name.startswith('measurement-bus-'):
                        bus = old_name.removeprefix('measurement-bus-')

                        new_process = multiprocessing.Process(
                            target=measurement_loop,
                            args=(
                                bus,
                                jobs,
                                jobs_in_progress,
                                results,
                                bus_info,
                                load_info,
                                MAXIMUM_WAIT,
                                MINIMUM_WAIT,
                                shutdown_event,
                                MAX_JOB_TIME
                            ),
                            name=old_name,
                        )

                    elif old_name == 'writer':
                        new_process = multiprocessing.Process(
                            target=writer_loop,
                            args=(
                                results,
                                DATA_PATH_BASE,
                                TZ_LOCAL,
                                MINIMUM_WAIT,
                                shutdown_event
                            ),
                            name='writer',
                        )

                    elif old_name == 'daily_loop':
                        new_process = multiprocessing.Process(
                            target=daily_loop,
                            name='daily_loop'
                        )
                    elif old_name == 'update_loop':
                        new_process = multiprocessing.Process(
                            target=update_loop,
                            name='update_loop'
                        )                       
                    else:
                        logger.error(f'unknown child process name {old_name}; cannot restart')
                        continue
                    
                    new_process.start()
                    processes[i] = new_process

                    logger.info(f'restarted {new_process.name}')

                # Reload the configuration
                try:
                    with open(CONFIG_PATH / 'measurement_config.json') as f:
                        new_config = json.load(f)
                except Exception:
                    logger.exception('Could not open the measurement config file')
                    new_config = config

                config = new_config

                #Log errror if measurement is not setup correctly
                for module in config['modules']:
                    tracer = module.get('tracer')
                    mounted_on = module.get('mounted_on')


                    if not tracer or not module.get('tracer').startswith('O'):
                        logger.error(f'{module.get("module_name")} does not have an expected tracer assigned')
                        module['tracer'] = 'XXXX'

                    if not mounted_on == "Egis-tracker" and not mounted_on == "Fixed-rack":
                        module['mounted_on'] = 'Unknown-rack'
                        logger.error(f'The mounted_on key of {module.get("module_name")} must be Fixed-rack or Egis-tracker')
                
                # OPETs are tracers that start with 'O'; these are the modules for OPETs only, ignoring other tracers
                modules = [
                    x for x in config['modules']
                    if x['tracer'].startswith('O')
                ]   

                # Add set_load_mode jobs
                for module in modules:
                    # check if module is enabled, and stopdate has yet not passed
                    module_enabled = not module.get('disabled', False)
                    module_not_expired = (
                        module.get('stopdate', None) is None
                        or datetime.datetime.strptime(module["stopdate"], "%Y-%m-%d").date() 
                        >= datetime.datetime.now().date())
                    
                    try:
                        if module_enabled and module_not_expired:  
                            if module.get('load_mode'):
                                scheduled_time = present() + datetime.timedelta(seconds=3)
                                jobs[job_id] = {
                                    'scheduled_time': scheduled_time,
                                    'expiration_time': schedule_update_time,
                                    'opet_name': module['tracer'],
                                    'opet_bus': load_info[module['tracer']]['bus'],
                                    'opet_address': load_info[module['tracer']]['address'],
                                    'module_name': module['module_name'],
                                    'mounted_on': module['mounted_on'],
                                    'axis_azimuth': config[module['mounted_on']]['axis_azimuth'],
                                    'axis_tilt': config[module['mounted_on']]['axis_tilt'],
                                    'job_type': 'set_load_mode',
                                    'load_mode': module['load_mode'],
                                    'disabled':  module['disabled']
                                }
                                logger.debug(f'manager: {job_id} (set_load_mode: {module["load_mode"]}) scheduled for {scheduled_time.astimezone(TZ_LOCAL)}')
                                job_id += 1
                            else:
                                logger.error(f'Could not set load_mode job due to missing key at Tracer {module.get("tracer")}')

                        else: # Module should be disabled
                            scheduled_time = present()
                            jobs[job_id] = {
                                'scheduled_time': scheduled_time,
                                'expiration_time': schedule_update_time,
                                'opet_name': module['tracer'],
                                'opet_bus': load_info[module['tracer']]['bus'],
                                'opet_address': load_info[module['tracer']]['address'],
                                'module_name': module['module_name'],
                                'mounted_on': module['mounted_on'],
                                'axis_azimuth': config[module['mounted_on']]['axis_azimuth'],
                                'axis_tilt': config[module['mounted_on']]['axis_tilt'],
                                'job_type': 'set_load_mode',
                                'load_mode': 'disable',
                                'disabled':  module['disabled']
                            }
                            logger.debug(f'manager: {job_id} (set_load_mode: disabled) scheduled for {scheduled_time.astimezone(TZ_LOCAL)}')
                            job_id += 1     
                    except Exception:
                        logger.exception("Exception in scheduling set_load job")                                         

                # Only schedule modules which are enabled
                modules = [
                    x for x in modules
                    if not x.get('disabled', False)
                ]

                # Only schedule modules which are not due
                modules = [
                    x for x in modules
                    if x.get("stopdate") is None or 
                    datetime.datetime.strptime(x["stopdate"], "%Y-%m-%d").date() >= datetime.datetime.now().date()
                ]      

                # Time to update the jobs list
                if jobs:
                    schedule_start = max([job['scheduled_time'] for job in jobs.values()])
                else:
                    schedule_start = present()
                schedule_end = present() + datetime.timedelta(seconds=SCHEDULE_HORIZON)

                for module in modules:
                    # Schedule point measurements
                    point_interval = module.get('interval_point')


                    if point_interval is None or type(point_interval) is not int:
                        logger.error(f'Could not schedule point measurement since interval_point is not set or not an integer for module {module.get("module_name")}')
                    
                    else:
                        point_interval = datetime.timedelta(seconds=point_interval)

                        # Find times in the scheduling window, spaced by the interval
                        scheduled_times = datetime_range(
                            schedule_start,
                            schedule_end,
                            point_interval,
                            include_start_point=False
                        )

                        # Jobs expire when they reach the scheduled time plus the
                        # measurement interval, because then they are redundant with
                        # the next repetition of the measurement.

                        # Eliminate times that would already have expired
                        scheduled_times = [
                            t
                            for t
                            in scheduled_times
                            if t + point_interval >= present()
                        ]

                        # Create a dict that gives the job instructions
                        for scheduled_time in scheduled_times:
                            jobs[job_id] = {
                                'scheduled_time': scheduled_time,
                                'expiration_time': scheduled_time + point_interval,
                                'opet_name': module['tracer'],
                                'opet_bus': load_info[module['tracer']]['bus'],
                                'opet_address': load_info[module['tracer']]['address'],
                                'module_name': module['module_name'],
                                'mounted_on': module['mounted_on'],
                                'axis_azimuth': config[module['mounted_on']]['axis_azimuth'],
                                'axis_tilt': config[module['mounted_on']]['axis_tilt'],
                                'job_type': 'point',
                                'data_destination': config['data_destination']
                            }
                            logger.debug(f'manager: {job_id} (point) scheduled for {scheduled_time.astimezone(TZ_LOCAL)}')
                            job_id += 1

                    # Schedule curve measurements
                    curve_interval = module.get('interval_curve')

                    if curve_interval is None or type(curve_interval) is not int:
                        logger.error(f'Could not schedule curve measurement since interval_curve is not set for module {module.get("module_name")}')

                    else:
                        curve_interval = datetime.timedelta(seconds=module.get('interval_curve'))

                        # Find times in the scheduling window, spaced by the interval
                        scheduled_times = datetime_range(
                            schedule_start,
                            schedule_end,
                            curve_interval,
                            include_start_point=False
                        )

                        # Eliminate times that would already have expired
                        scheduled_times = [
                            t
                            for t
                            in scheduled_times
                            if t + curve_interval >= present()
                        ]

                        # Create a dict that gives the job instructions
                        for scheduled_time in scheduled_times:
                            jobs[job_id] = {
                                'scheduled_time': scheduled_time,
                                'expiration_time': scheduled_time + curve_interval,
                                'opet_name': module['tracer'],
                                'opet_bus': load_info[module['tracer']]['bus'],
                                'opet_address': load_info[module['tracer']]['address'],
                                'module_name': module['module_name'],
                                'mounted_on': module['mounted_on'],
                                'axis_azimuth': config[module['mounted_on']]['axis_azimuth'],
                                'axis_tilt': config[module['mounted_on']]['axis_tilt'],
                                'job_type': 'curve',
                                'data_destination': config['data_destination']
                            }
                            logger.debug(f'manager: {job_id} (curve) scheduled for {scheduled_time.astimezone(TZ_LOCAL)}')
                            job_id += 1

                # Remove jobs that have been waiting too long
                for old_job_id, old_job in list(jobs.items()):
                    if present() - old_job["scheduled_time"] > MAX_JOB_TIME:
                        logger.error(f'Removed job {old_job_id}: {old_job["job_type"]} for {old_job.get("opet_name")}: too old')
                        try:
                            jobs.pop(old_job_id)
                        except KeyError:
                            pass
    
                logger.debug(f'manager: {len(jobs)} jobs are scheduled')
                logger.debug(f'manager: {jobs.keys()}')


        # Handle Exceptions
        except KeyboardInterrupt:
            logger.error('Keyboard interrupt, shutting down child processes')
            shutdown_event.set()  
        except Exception as e:
            logger.exception(f'shutting down child processes; {e}')
            shutdown_event.set()   
        finally:
            shutdown_event.set()            
            # Wait for processes to finish 
            for process in processes:
                process.join(timeout=10)  
            
            # Otherwise terminate remaining processes
            for process in processes:  
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)