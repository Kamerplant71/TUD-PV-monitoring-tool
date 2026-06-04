import csv
from tud_pv_monitoring.measurement_scheduling_tools import present
from OPET_control import OPETBus, OPET, OPETTimeoutError, UnexpectedReplyError
from tud_pv_monitoring.serial_by_serial import device_name
from serial import Serial
import logging


logger = logging.getLogger(__name__)


def measurement_loop(bus, jobs, jobs_in_progress, results, bus_info, load_info, maximum_wait, minimum_wait, shutdown_event, max_job_time):
    """ Run scheduled measurement jobs for one OPET bus. This loop handles jobs assigned
        to one bus and stores completed measurements in the shared results dictionary.
    
    Args:
        bus (str): Identifier of the OPET bus
        jobs (DictProxy): Shared dictionary containing information of all scheduled jobs
        jobs_in_progress (DictProxy): Shared dictionary containing the curve jobs that are in progress
        results (DictProxy): Shared dictionary containg the results for the writer
        bus_info (dict): Dictionary containing the adapter serial number connected to the bus
        load_info (dict): Dictionary containing to which bus each OPET is connected to 
        maximum_wait (float): Maximum number of seconds into the future that
            it will wait for a job before returning to the main loop.
        minimum_wait (float): Minimum delay before returning the main loop 
        shutdown_event (multiprocessing.Event): In case of shutting down this event is set
        max_job_time (datetime.timedelta): Maximum time a job should live after the schedule time
    """

    def initialize_bus():
        """Set up the OPET bus serial communication

        Returns:
            Tuple : a list of individual OPETs and
                the serial port. Returns None, None if something goes wrong.

        """

        try:
            serial_port_name = device_name(bus_info[bus]['adapter_serial_number'])[0]
            serial_port = Serial(serial_port_name, baudrate=200000, timeout=1)
            opet_bus = OPETBus(serial_port)
            this_bus_opet_addresses = [
                v['address']
                for k, v
                in load_info.items()
                if v['bus'] == bus
            ]
            opets = {
                address: OPET(opet_bus, address, iv_time_multiplier=2)
                for address
                in this_bus_opet_addresses
            }
            return opets, serial_port
        except IndexError:
            logging.error(f'Couldn\'t find the serial port for bus {bus}')
            return None, None
        except OPETTimeoutError:
            logging.error(f'Couldn\'t connect to OPET on bus {bus}')
            return None, None
        except UnexpectedReplyError:
            logging.error(f'Got unexpected reply while connecting to OPET on bus {bus}')
            return None, None  
        except Exception as e:
            logging.error(f'Caught exception while connecting to OPET on bus {bus}: {e}')
            return None, None

    try:
        # Attempt to set up the bus 
        opets, serial_port = initialize_bus()

        # Do jobs as they are scheduled and become due
        while not shutdown_event.is_set():
            if opets is None:
                # Attempt to set up the bus
                opets, serial_port = initialize_bus()
                # If it failed, wait before retrying
                if opets is None:
                    shutdown_event.wait(5)
                # Return to the top of the loop
                continue
            
            # These jobs belong to this bus, so other processes will not touch them
            bus_jobs = {
                job_id: job
                for job_id, job
                in jobs.items()
                if job['opet_bus'] == bus
            }

            # Consider each job that is scheduled for this bus
            # Sort by scheduled time
            bus_jobs = dict(sorted(bus_jobs.items(), key=lambda x: x[1]['scheduled_time']))
            this_bus_job_ids = list(bus_jobs.keys())

            # Repeat this as long as there are job ids listed for this bus
            while this_bus_job_ids:
                # Consider the first remaining job in this_bus_job_ids
                job_id = this_bus_job_ids[0]

                try:
                    job = jobs[job_id]
                except KeyError:
                    logger.error(f'bus {bus}: job {job_id}: job disappeared before processing')
                    this_bus_job_ids.pop(0)
                    continue            



                
                # Determine how far into the future this job should be scheduled
                seconds_until_target = (
                    job['scheduled_time'] - present()
                ).total_seconds()

                # Ignore this job if it's too far into the future
                if seconds_until_target > maximum_wait:
                    # All other jobs are later than this, so this loop is finished
                    break
                
                # It's time to do this job. Pop it out of the shared jobs dict and
                # this bus's job id list.
                this_bus_job_ids.pop(0)

                try: 
                    job = jobs.pop(job_id)
                except KeyError:
                    logger.warning(f'bus {bus}: job {job_id}: job disappeared before execution')
                    continue

                # Delay until the right moment
                if shutdown_event.wait(max(0, seconds_until_target)):
                    break # If shutdown is set, break the loop

                # Check if this OPET is available:
                if not opets[job['opet_address']].available:
                    logger.debug(f'bus {bus}: job {job_id}: delayed because {job["opet_name"]} is not available')
                    # The OPET isn't available (measuring a curve) so we'll
                    # put this job back on the shared dictionary, but not back in 
                    # `this_bus_job_ids`, so we won't try again until the next loop
                    jobs[job_id] = job
                    # Return to the top of the loop to handle the next job in
                    # `this_bus_job_ids`
                    continue

                # Handle the job types differently
                # Set load mode, also enable output if disable is false
                if job['job_type'] == 'set_load_mode':
                    # mppt
                    if job["load_mode"] == 'mppt':
                        try:
                            opets[job['opet_address']].mode = 'mppt'
                            opets[job['opet_address']].output_enabled = True
                        except OPETTimeoutError:
                            logger.error(f'bus {bus}: job {job_id}: OPETTimeoutError in `set_load_mode` on {job["opet_name"]}; Got an empty reply on the OPET bus')  
                        except UnexpectedReplyError:
                            logger.error(f'bus {bus}: job {job_id}: UnexpectedReplyError in `set_load_mode` on {job["opet_name"]}; Got an unexpected reply the OPET bus')  
                        except Exception as e:
                            logger.error(f'bus {bus}: job {job_id}: Unexpected error in `set_load_mode` on {job["opet_name"]}; {e}')  

                    # voc
                    elif job["load_mode"] == 'voc':
                        try:
                            opets[job['opet_address']].mode = 'voc'
                            opets[job['opet_address']].output_enabled = True    
                        except OPETTimeoutError:
                            logger.error(f'bus {bus}: job {job_id}: OPETTimeoutError in `set_load_mode` on {job["opet_name"]}; Got an empty reply on the OPET bus') 
                        except UnexpectedReplyError:
                            logger.error(f'bus {bus}: job {job_id}: UnexpectedReplyError in `set_load_mode` on {job["opet_name"]}; Got an unexpected reply the OPET bus')  
                        except Exception as e:
                            logger.error(f'bus {bus}: job {job_id}: Unexpected error in `set_load_mode` on {job["opet_name"]}; {e}')  

                    # isc
                    elif job["load_mode"] == 'isc':
                        try:
                            opets[job['opet_address']].mode = 'isc'
                            opets[job['opet_address']].output_enabled = True
                        except OPETTimeoutError:
                            logger.error(f'bus {bus}: job {job_id}: OPETTimeoutError in `set_load_mode` on {job["opet_name"]}; Got an empty reply on the OPET bus') 
                        except UnexpectedReplyError:
                            logger.error(f'bus {bus}: job {job_id}: UnexpectedReplyError in `set_load_mode` on {job["opet_name"]}; Got an unexpected reply the OPET bus')  
                        except Exception as e:
                            logger.error(f'bus {bus}: job {job_id}: Unexpected error in `set_load_mode` on {job["opet_name"]}; {e}')  

                    # disable output
                    elif job["load_mode"] == 'disable':
                        try:
                            opets[job['opet_address']].output_enabled = False
                        except OPETTimeoutError:
                            logger.error(f'bus {bus}: job {job_id}: OPETTimeoutError in disable OPET on {job["opet_name"]}; Got an empty reply on the OPET bus')
                        except UnexpectedReplyError:
                            logger.error(f'bus {bus}: job {job_id}: UnexpectedReplyError in `set_load_mode` on {job["opet_name"]}; Got an unexpected reply the OPET bus')  
                        except Exception as e:
                            logger.error(f'bus {bus}: job {job_id}: Unexpected error in `set_load_mode` on {job["opet_name"]}; {e}')  

                # Do point measurement
                elif job['job_type'] == 'point':
                    # Point measurements are requested and recorded in immediate
                    # succession

                    # Do the job
                    try:
                        result = opets[job['opet_address']].sample
                        logger.debug(f'bus {bus}: job {job_id}: {result["voltage"], result["current"]}, {present() - job["scheduled_time"]} late')
                        # Store the results
                        results[job_id] = {
                            'date_time': present(),
                            'scheduled_time': job['scheduled_time'],
                            'measurement_type': job['job_type'],
                            'v': result['voltage'],
                            'i': result['current'],
                            'temperature_cell': result['temperature_cell'],
                            'module_name': job['module_name'],
                            'mounted_on': job['mounted_on'],
                            'axis_azimuth': job['axis_azimuth'],
                            'axis_tilt': job['axis_tilt'],
                            'status_integer': result['status_integer'],
                            'data_destination': job['data_destination']
                        }
                    # We may get ValueError if the load returned something that
                    # isn't a valid float, for example
                    except ValueError: 
                        logger.error(f'bus {bus}: job {job_id}: ValueError in `sample` property on load {job["opet_name"]}; couldn\'t parse the load\'s reply')
                        # The job has already been taken off the jobs list

                    #OPET may give error
                    except OPETTimeoutError:
                        logger.error(f'bus {bus}: job {job_id}: OPETTimeoutError in `sample` property on load {job["opet_name"]}; Got an empty reply on the OPET bus')
                    except UnexpectedReplyError:
                        logger.error(f'bus {bus}: job {job_id}: UnexpectedReplyError in `sample` property on load {job["opet_name"]}; Got an unexpected reply the OPET bus')  
                    except Exception as e:
                        logger.error(f'bus {bus}: job {job_id}: Unexpected error in `sample` property on load  {job["opet_name"]}; {e}')  

                elif job['job_type'] == 'curve':
                    # Curve measurements are first all requested as scheduled,
                    # then collected and recorded later in a separate step
                    # Request the curve
                    try:
                        reply = opets[job['opet_address']].start_iv_curve()
                        if reply:
                            logger.debug(f'bus {bus}: job {job_id}: curve measurement (start) {job_id}, {present() - job["scheduled_time"]} late')

                            # Add the job to jobs_in_progress
                            job['date_time'] = present()
                            job['measurement_duration'] = reply
                            jobs_in_progress[job_id] = job
                        else:
                            logger.debug(f'bus {bus}: job {job_id}: curve measurement (not started) {job_id}, {present() - job["scheduled_time"]} late')
                    except OPETTimeoutError:
                        logger.error(f'bus {bus}: job {job_id}: OPETTimeoutError in start_iv_curve property on load {job["opet_name"]}; Got an empty reply on the OPET bus')
                    except UnexpectedReplyError:
                        logger.error(f'bus {bus}: job {job_id}: UnexpectedReplyError in start_iv_curve property on load {job["opet_name"]}; Got an unexpected reply the OPET bus')  
                    except Exception as e:
                        logger.error(f'bus {bus}: job {job_id}: Unexpected error in start_iv_curve property on load {job["opet_name"]}; {e}')  

            # Now check whether the jobs in progress are complete, reading back
            # results as they become available

            # These jobs belong to this bus, so other threads will not touch them
            bus_jobs_in_progress = {
                job_id: job
                for job_id, job
                in jobs_in_progress.items()
                if job['opet_bus'] == bus
            }
            # Sort by scheduled time so we can FIFO
            bus_jobs_in_progress = dict(sorted(bus_jobs_in_progress.items(), key=lambda x: x[1]['scheduled_time']))
            for job_id, _ in bus_jobs_in_progress.items():
                # Pop a job out of the shared in-progress jobs dictionary
                try:
                    job = jobs_in_progress.pop(job_id)
                except KeyError:
                    logger.warning(f'bus {bus}: job {job_id}: in-progress job disappeared before checking')
                    continue                
                # logger.debug(f'bus {bus}: job {job_id}: curve measurement (check if ready) {present() - job["scheduled_time"]} late')
                # Check if the measurement is complete
                if not opets[job['opet_address']].available:
                    measurement_complete = False
                else:
                    try:
                        measurement_complete = opets[job['opet_address']].operation_complete()
                    except OPETTimeoutError:
                        measurement_complete = False
                        logger.error(f'bus {bus}: job {job_id}: OPETTimeoutError in check operation complete status on {job["opet_name"]}; Got an empty reply on the OPET bus')
                    except UnexpectedReplyError:
                        logger.error(f'bus {bus}: job {job_id}: UnexpectedReplyError in check operation complete status on {job["opet_name"]}; Got an unexpected reply the OPET bus')  
                        measurement_complete = False
                    except Exception as e:
                        logger.error(f'bus {bus}: job {job_id}: Unexpected error in check operation complete status on {job["opet_name"]};{e}')  
                        measurement_complete = False

                if measurement_complete:
                    # The measurement is ready
                    logger.debug(f'bus {bus}: job {job_id}: curve measurement (complete) {present() - job["scheduled_time"]} late')
                    # Read the result
                    try:
                        result = opets[job['opet_address']].iv_data
                        # Store the result
                        results[job_id] = {
                            'scheduled_time': job['scheduled_time'],
                            'date_time': job['date_time'],
                            'measurement_duration': job['measurement_duration'],
                            'measurement_type': job['job_type'],
                            'module_name': job['module_name'],
                            'mounted_on': job['mounted_on'],
                            'axis_azimuth': job['axis_azimuth'],
                            'axis_tilt': job['axis_tilt'],
                            'v': result['voltage'],
                            'i': result['current'],
                            'iv_status_integer': result['iv_status'],
                            'temperature_cell': result['temperature_cell'],
                            'data_destination': job['data_destination']
                        }
                    # We may get ValueError if the load returned something that
                    # isn't a valid float, for example
                    except ValueError: 
                        logger.error(f'bus {bus}: job {job_id}: ValueError in `iv_data` property on load {job["opet_name"]}; couldn\'t parse the load\'s reply')
                        # The job has already been taken off the jobs_in_progress
                        # list
                    except OPETTimeoutError:
                        logger.error(f'bus {bus}: job {job_id}: OPETTimeoutError in `iv_data` property on load {job["opet_name"]}; Got an empty reply on the OPET bus')
                    except UnexpectedReplyError:
                        logger.error(f'bus {bus}: job {job_id}: UnexpectedReplyError in `iv_data` property on load {job["opet_name"]}; Got an unexpected reply the OPET bus')  
                    except Exception as e:
                        logger.error(f'bus {bus}: job {job_id}: Unexpected error in `iv_data` property on load {job["opet_name"]}; {e}')  
                else:
                    # The measurement is not yet ready
                    # logger.debug(f'bus {bus}: job {job_id}: curve measurement (not yet ready) {present() - job["scheduled_time"]} late')
                    # We popped this job out of the dict, now we put it back
                    jobs_in_progress[job_id] = job
                    continue
            shutdown_event.wait(minimum_wait)

    except Exception:
        logger.exception(f'bus {bus}: Exception in measurement_loop')
        raise
    finally:
        if serial_port is not None:
            try:
                serial_port.close()
                logger.info(f'bus {bus}: serial port closed')
            except Exception:
                logger.exception(f'bus {bus}: failed to close serial port')


def writer_loop(results, data_path_base, tz_local, minimum_wait, shutdown_event):
    """Writer loop that writes the contents of results into the .csv file

    Args:
        results (DictProxy): contains all the recent results for storing
        dath_path (Path): Path to where the data should be stored
        TZ_LOCAL (ZoneInfo): Timezone information
        MINIMUM_WAIT (float): Infinite loops with nothing to do will delay this much before checking again
        shutdown_event (multiprocessing.Event): In case of shutting down this event is set
    """

    headers = {
        'point': [
            'date_time',  
            'scheduled_time',    
            'module_name',       
            'mounted_on',
            'v',                 
            'i',                 
            'status_integer',
            'temperature_cell',    
            'axis_azimuth',  
            'axis_tilt'   
        ],
        'curve': [
            'date_time',
            'scheduled_time',
            'measurement_duration',
            'module_name',
            'mounted_on',
            'v',
            'i',
            'iv_status_integer',
            'temperature_cell',
            'axis_azimuth',
            'axis_tilt',
        ]
    }

    try:
        while not shutdown_event.is_set() or results: # Write until no more results and shutdown is set
            while results:
                try: 
                    result = results.pop(next(iter(results.keys())))
                except KeyError:
                    continue

                measurement_date = result['date_time'].astimezone(tz_local).date().isoformat()
                data_path = data_path_base / measurement_date / result['data_destination']
                data_file_path = (
                    data_path / (
                        'opet_results_'
                        + result['measurement_type']
                        + '_' + measurement_date
                        + '.csv'
                    )
                )

                # Convert datetimes to ISO 8601 strings
                result['date_time'] = result['date_time'].astimezone(tz_local).isoformat()
                result['scheduled_time'] = result['scheduled_time'].astimezone(tz_local).isoformat()


                # Create the log file directory, if necessary
                data_file_path.parent.mkdir(parents=True, exist_ok=True)

                # Decide whether to write headers
                need_headers = not data_file_path.exists()
                with open(data_file_path, 'a', newline='') as log:
                    writer = csv.DictWriter(
                        log,
                        fieldnames=headers[result['measurement_type']],
                        extrasaction='ignore'
                    )
                    if need_headers:
                        writer.writeheader()
                    writer.writerow(result)
            shutdown_event.wait(minimum_wait)
    except Exception as e:
        logger.exception(f'writer_loop: exception error; {e}')
        raise       



