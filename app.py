import ts3
import time
import os
import argparse
from prometheus_client import start_http_server, Counter, Gauge

READ_INTERVAL_IN_SECONDS = 5
METRICS_PREFIX = 'teamspeak_'

METRICS_NAMES = [
    'connection_bandwidth_received_last_minute_total',
    'connection_bandwidth_received_last_second_total',
    'connection_bandwidth_sent_last_minute_total',
    'connection_bandwidth_sent_last_second_total',
    'connection_bytes_received_control',
    'connection_bytes_received_keepalive',
    'connection_bytes_received_speech',
    'connection_bytes_received_total',
    'connection_bytes_sent_control',
    'connection_bytes_sent_keepalive',
    'connection_bytes_sent_speech',
    'connection_bytes_sent_total',
    'connection_filetransfer_bandwidth_received',
    'connection_filetransfer_bandwidth_sent',
    'connection_filetransfer_bytes_received_total',
    'connection_filetransfer_bytes_sent_total',
    'connection_packets_received_control',
    'connection_packets_received_keepalive',
    'connection_packets_received_speech',
    'connection_packets_received_total',
    'connection_packets_sent_control',
    'connection_packets_sent_keepalive',
    'connection_packets_sent_speech',
    'connection_packets_sent_total',
    'virtualserver_channelsonline',
    'virtualserver_client_connections',
    'virtualserver_clientsonline',
    'virtualserver_maxclients',
    'virtualserver_month_bytes_downloaded',
    'virtualserver_month_bytes_uploaded',
    'virtualserver_query_client_connections',
    'virtualserver_queryclientsonline',
    'virtualserver_reserved_slots',
    'virtualserver_total_bytes_downloaded',
    'virtualserver_total_bytes_uploaded',
    'virtualserver_total_packetloss_control',
    'virtualserver_total_packetloss_keepalive',
    'virtualserver_total_packetloss_speech',
    'virtualserver_total_packetloss_total',
    'virtualserver_total_ping',
    'virtualserver_uptime'
]

PROMETHEUS_METRICS = {}

class Teamspeak3MetricService:
    def __init__(self, host, port, username, password):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.configure_via_environment_variables()

        print('TS3 SETTINGS:\nHost: %s\nPort: %s\nUsername: %s\nPassword: %s' % (self.host, self.port, self.username, self.password))

        for teamspeak_metric_name in METRICS_NAMES:
            PROMETHEUS_METRICS[teamspeak_metric_name] = Gauge(METRICS_PREFIX + teamspeak_metric_name, METRICS_PREFIX + teamspeak_metric_name, ['virtualserver_name'])
            print('Initialized gauge %s' % teamspeak_metric_name)

    def configure_via_environment_variables(self):
        if os.environ.get('TEAMSPEAK_HOST') is not None:
            self.host = os.environ.get('TEAMSPEAK_HOST')

        if os.environ.get('TEAMSPEAK_PORT') is not None:
            self.port = os.environ.get('TEAMSPEAK_PORT')
        
        if os.environ.get('TEAMSPEAK_USERNAME') is not None:
            self.username = os.environ.get('TEAMSPEAK_USERNAME')
        
        if os.environ.get('TEAMSPEAK_PASSWORD') is not None:
            self.password = os.environ.get('TEAMSPEAK_PASSWORD')

    def connect(self):
        self.serverQueryService = ts3.TS3Server(self.host, self.port)
        isLoginSuccessful = self.serverQueryService.login(self.username, self.password)

        if not isLoginSuccessful:
            raise()
            print('Login not successful')
            exit(1)

    def read(self):
        serverlistResponse = self.serverQueryService.serverlist()
        if not serverlistResponse.response['msg'] == 'ok':
            print('Error retrieving serverlist: %s' % serverlistResponse.response['msg'])
            return

        servers = serverlistResponse.data

        for server in servers:
            virtualserver_id = server.get('virtualserver_id')
            self.serverQueryService.use(virtualserver_id)
            serverinfoResponse = self.serverQueryService.send_command('serverinfo')
            if not serverinfoResponse.response['msg'] == 'ok':
                print('Error retrieving serverinfo: %s' % serverinfoResponse.response['msg'])
                return

            serverinfo = serverinfoResponse.data[0]
            virtualserver_name = serverinfo['virtualserver_name']
            
            for teamspeak_metric_name in METRICS_NAMES:
                PROMETHEUS_METRICS[teamspeak_metric_name].labels(virtualserver_name=virtualserver_name).set(serverinfo[teamspeak_metric_name])
    
    def disconnect(self):
        self.serverQueryService.disconnect()

parser = argparse.ArgumentParser()
parser.add_argument('--ts3host', help='Hostname or ip address of TS3 server', type=str, default='localhost')
parser.add_argument('--ts3port', help='Port of TS3 server', type=int, default=10011)
parser.add_argument('--ts3username', help='ServerQuery username of TS3 server', type=str, default='serveradmin')
parser.add_argument('--ts3password', help='ServerQuery password of TS3 server', type=str, default='')
parser.add_argument('--metricsport', help='Port on which this service exposes the metrics', type=int, default=8000)
args = parser.parse_args()

if os.environ.get('METRICS_PORT') is None:
    metrics_port = args.metricsport
else:
    metrics_port = os.environ.get('METRICS_PORT')

ts3Service = Teamspeak3MetricService(host=args.ts3host, port=args.ts3port, username=args.ts3username, password=args.ts3password)
ts3Service.configure_via_environment_variables()
start_http_server(metrics_port)
print('Started metrics endpoint on port %s' % metrics_port)
while True:
    print('Fetching metrics')
    ts3Service.connect()
    ts3Service.read()
    ts3Service.disconnect()
    time.sleep(READ_INTERVAL_IN_SECONDS)
