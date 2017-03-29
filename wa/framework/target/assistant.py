import logging
import os
import shutil
import sys
import tempfile
import threading
import time

from wa import Parameter
from wa.framework.exception import WorkerThreadError


class LinuxAssistant(object):

    parameters = []

    def __init__(self, target):
        self.target = target

    def start(self):
        pass

    def extract_results(self, context):
        pass

    def stop(self):
        pass


class AndroidAssistant(object):

    parameters = [
        Parameter('logcat_poll_period', kind=int,
                  constraint=lambda x: x > 0,
                  description="""
                  Polling period for logcat in seconds. If not specified,
                  no polling will be used.

                  Logcat buffer on android is of limited size and it cannot be
                  adjusted at run time. Depending on the amount of logging activity,
                  the buffer may not be enought to capture comlete trace for a
                  workload execution. For those situations, logcat may be polled
                  periodically during the course of the run and stored in a
                  temporary locaiton on the host. Setting the value of the poll
                  period enables this behavior.
                  """),
    ]

    def __init__(self, target, logcat_poll_period=None):
        self.target = target
        if logcat_poll_period:
            self.logcat_poller = LogcatPoller(target, logcat_poll_period)
        else:
            self.logcat_poller = None

    def start(self):
        if self.logcat_poller:
            self.logcat_poller.start()

    def stop(self):
        if self.logcat_poller:
            self.logcat_poller.stop()

    def extract_results(self, context):
        logcat_file = os.path.join(context.output_directory, 'logcat.log')
        self.dump_logcat(logcat_file)
        context.add_artifact('logcat', logcat_file, kind='log')
        self.clear_logcat()

    def dump_logcat(self, outfile):
        if self.logcat_poller:
            self.logcat_poller.write_log(outfile)
        else:
            self.target.dump_logcat(outfile)

    def clear_logcat(self):
        if self.logcat_poller:
            self.logcat_poller.clear_buffer()


class LogcatPoller(threading.Thread):

    def __init__(self, target, period=60, timeout=30):
        super(LogcatPoller, self).__init__()
        self.target = target
        self.logger = logging.getLogger('logcat')
        self.period = period
        self.timeout = timeout
        self.stop_signal = threading.Event()
        self.lock = threading.Lock()
        self.buffer_file = tempfile.mktemp()
        self.last_poll = 0
        self.daemon = True
        self.exc = None

    def start(self):
        self.logger.debug('starting polling')
        try:
            while True:
                if self.stop_signal.is_set():
                    break
                with self.lock:
                    current_time = time.time()
                    if (current_time - self.last_poll) >= self.period:
                        self.poll()
                time.sleep(0.5)
        except Exception:  # pylint: disable=W0703
            self.exc = WorkerThreadError(self.name, sys.exc_info())
        self.logger.debug('polling stopped')

    def stop(self):
        self.logger.debug('Stopping logcat polling')
        self.stop_signal.set()
        self.join(self.timeout)
        if self.is_alive():
            self.logger.error('Could not join logcat poller thread.')
        if self.exc:
            raise self.exc  # pylint: disable=E0702

    def clear_buffer(self):
        self.logger.debug('clearing logcat buffer')
        with self.lock:
            self.target.clear_logcat()
            with open(self.buffer_file, 'w') as _:  # NOQA
                pass

    def write_log(self, outfile):
        with self.lock:
            self.poll()
            if os.path.isfile(self.buffer_file):
                shutil.copy(self.buffer_file, outfile)
            else:  # there was no logcat trace at this time
                with open(outfile, 'w') as _:  # NOQA
                    pass

    def close(self):
        self.logger.debug('closing poller')
        if os.path.isfile(self.buffer_file):
            os.remove(self.buffer_file)

    def poll(self):
        self.last_poll = time.time()
        self.target.dump_logcat(self.buffer_file, append=True, timeout=self.timeout)
        self.target.clear_logcat()