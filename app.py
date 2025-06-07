import ts3
import time
import os
import argparse
from prometheus_client import start_http_server, Counter, Gauge

READ_INTERVAL_IN_SECONDS = 60
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

        print('TS3 SETTINGS:\nHost: %s\nPort: %s\nUsername: %s\nPassword: *censored*' % (self.host, self.port,
                                                                                         self.username))

        for teamspeak_metric_name in METRICS_NAMES:
            PROMETHEUS_METRICS[teamspeak_metric_name] = Gauge(METRICS_PREFIX + teamspeak_metric_name,
                                                              METRICS_PREFIX + teamspeak_metric_name,
                                                              ['virtualserver_name'])
            print('Initialized gauge %s' % teamspeak_metric_name)

        # Add new gauge for online players
        self.player_online = Gauge('teamspeak_player_online', 'Online players',
                                   [
                                       'virtualserver_name',
                                       'player_id',
                                       'nickname',
                                       'clid',
                                       'cid',
                                       'client_database_id',
                                       'client_nickname',
                                       'client_type',
                                       'client_away',
                                       'client_away_message',
                                       'client_flag_talking',
                                       'client_input_muted',
                                       'client_output_muted',
                                       'client_input_hardware',
                                       'client_output_hardware',
                                       'client_talk_power',
                                       'client_is_talker',
                                       'client_is_priority_speaker',
                                       'client_is_recording',
                                       'client_is_channel_commander',
                                       'client_unique_identifier',
                                       'client_servergroups',
                                       'client_channel_group_id',
                                       'client_channel_group_inherited_channel_id',
                                       'client_version',
                                       'client_platform',
                                       'client_idle_time',
                                       'client_created',
                                       'client_lastconnected',
                                       'client_country',
                                       'connection_client_ip',
                                       'client_badges',
                                   ])

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
            raise ()
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
                continue  # Continue to next server

            serverinfo = serverinfoResponse.data[0]
            virtualserver_name = serverinfo['virtualserver_name']

            for teamspeak_metric_name in METRICS_NAMES:
                if teamspeak_metric_name in serverinfo:
                    PROMETHEUS_METRICS[teamspeak_metric_name].labels(virtualserver_name=virtualserver_name).set(
                        serverinfo[teamspeak_metric_name])

            # Get online players for this server
            clientlistResponse = self.serverQueryService.send_command(
                'clientlist -uid -away -voice -times -groups -info -country -ip -badges')
            if clientlistResponse.response['msg'] == 'ok':
                players = clientlistResponse.data
                for player in players:
                    player_id = player.get('player_id')
                    nickname = player.get('nickname')
                    clid = player.get('clid')
                    cid = player.get('cid')
                    client_database_id = player.get('client_database_id')
                    client_nickname = player.get('client_nickname')
                    client_type = player.get('client_type')
                    client_away = player.get('client_away')
                    client_away_message = player.get('client_away_message')
                    client_flag_talking = player.get('client_flag_talking')
                    client_input_muted = player.get('client_input_muted')
                    client_output_muted = player.get('client_output_muted')
                    client_input_hardware = player.get('client_input_hardware')
                    client_output_hardware = player.get('client_output_hardware')
                    client_talk_power = player.get('client_talk_power')
                    client_is_talker = player.get('client_is_talker')
                    client_is_priority_speaker = player.get('client_is_priority_speaker')
                    client_is_recording = player.get('client_is_recording')
                    client_is_channel_commander = player.get('client_is_channel_commander')
                    client_unique_identifier = player.get('client_unique_identifier')
                    client_servergroups = player.get('client_servergroups')
                    client_channel_group_id = player.get('client_channel_group_id')
                    client_channel_group_inherited_channel_id = player.get('client_channel_group_inherited_channel_id')
                    client_version = player.get('client_version')
                    client_platform = player.get('client_platform')
                    client_idle_time = player.get('client_idle_time')
                    client_created = player.get('client_created')
                    client_lastconnected = player.get('client_lastconnected')
                    client_country = player.get('client_country')
                    connection_client_ip = player.get('connection_client_ip')
                    client_badges = player.get('client_badges')

                    if "serveradmin" == client_nickname:
                        continue

                    if clid and client_nickname:
                        self.player_online.labels(virtualserver_name=virtualserver_name,
                                                  player_id=player_id,
                                                  nickname=nickname,
                                                  clid=clid,
                                                  cid=cid,
                                                  client_database_id=client_database_id,
                                                  client_nickname=client_nickname,
                                                  client_type=client_type,
                                                  client_away=client_away,
                                                  client_away_message=client_away_message,
                                                  client_flag_talking=client_flag_talking,
                                                  client_input_muted=client_input_muted,
                                                  client_output_muted=client_output_muted,
                                                  client_input_hardware=client_input_hardware,
                                                  client_output_hardware=client_output_hardware,
                                                  client_talk_power=client_talk_power,
                                                  client_is_talker=client_is_talker,
                                                  client_is_priority_speaker=client_is_priority_speaker,
                                                  client_is_recording=client_is_recording,
                                                  client_is_channel_commander=client_is_channel_commander,
                                                  client_unique_identifier=client_unique_identifier,
                                                  client_servergroups=client_servergroups,
                                                  client_channel_group_id=client_channel_group_id,
                                                  client_channel_group_inherited_channel_id=client_channel_group_inherited_channel_id,
                                                  client_version=client_version,
                                                  client_platform=client_platform,
                                                  client_idle_time=client_idle_time,
                                                  client_created=client_created,
                                                  client_lastconnected=client_lastconnected,
                                                  client_country=client_country,
                                                  connection_client_ip=connection_client_ip,
                                                  client_badges=client_badges,
                                                  ).set(1)

    def disconnect(self):
        self.serverQueryService.disconnect()


parser = argparse.ArgumentParser()
parser.add_argument('--ts3host', help='Hostname or ip address of TS3 server', type=str, default='10.115.15.25')
parser.add_argument('--ts3port', help='Port of TS3 server', type=int, default=10011)
parser.add_argument('--ts3username', help='ServerQuery username of TS3 server', type=str, default='serveradmin')
parser.add_argument('--ts3password', help='ServerQuery password of TS3 server', type=str, default='Dd112211')
parser.add_argument('--metricsport', help='Port on which this service exposes the metrics', type=int, default=8000)
args = parser.parse_args()

if os.environ.get('METRICS_PORT') is None:
    metrics_port = args.metricsport
else:
    metrics_port = os.environ.get('METRICS_PORT')

ts3Service = Teamspeak3MetricService(host=args.ts3host, port=args.ts3port, username=args.ts3username,
                                     password=args.ts3password)
ts3Service.configure_via_environment_variables()
start_http_server(metrics_port)
print('Started metrics endpoint on port %s' % metrics_port)
while True:
    print('Fetching metrics')
    ts3Service.connect()
    ts3Service.read()
    ts3Service.disconnect()
    time.sleep(READ_INTERVAL_IN_SECONDS)