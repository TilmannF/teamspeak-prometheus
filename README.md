# teamspeak-prometheus

Teamspeak metrics for Prometheus

## Description
The Python script starts an HTTP-server, which exposes the Prometheus metrics & queries a given TS3 server via Teamspeak's ServerQuery procotol at an interval. The metrics provided by Teamspeak aren't convertered and passed as they are.

## Usage
### Without Docker
#### Requirements
* Python 3
* PIP

#### Installation
1. Clone this repository
2. `cd` into the cloned repository
3. Run `pip install -r requirements.txt`

Bonus points: use virtualenv

#### Arguments
List all arguments with `python -h`

| Name | Description | Default value |
| --- | --- | --- |
| `--ts3host` | Hostname or ip address of TS3 server | *localhost* |
| `--ts3port` | Port of TS3 server | *10011* |
| `--ts3username` | ServerQuery username of TS3 server | *serveradmin* |
| `--ts3password` | ServerQuery password of TS3 server |  |
| `--metricsport` | Port on which this service exposes the metrics | *8000* |

Example: `python app.py --ts3host example.com --ts3port 12345 --ts3username ExampleUser --ts3password SomePassword --metricsport 8080`

### With Docker (recommended)
#### Requirements
* Docker

#### Installation
With docker:
`docker run -d -p 8000:8000 -e TEAMSPEAK_HOST='123.123.123.123' -e TEAMSPEAK_PASSWORD='example123' tilmannf/teamspeak-prometheus` 

With docker-compose:
```version: '3'
services:
  teamspeak-prometheus:
    image: teamspeak-prometheus:latest
    environment:
      TEAMSPEAK_HOST: 123.123.123.123
      TEAMSPEAK_PASSWORD: example123
    ports:
      - 8000:8000
```

#### Environment variables

| Name | Description | Default value |
| --- | --- | --- |
| `TEAMSPEAK_HOST` | Hostname or ip address of TS3 server | *localhost* |
| `TEAMSPEAK_PORT` | Port of TS3 server | *10011* |
| `TEAMSPEAK_USERNAME` | ServerQuery username of TS3 server | *serveradmin* |
| `TEAMSPEAK_PASSWORD` | ServerQuery password of TS3 server |  |

### Prometheus configuration
```- job_name: teamspeak
  honor_timestamps: true
  scrape_interval: 15s
  scrape_timeout: 10s
  metrics_path: /metrics
  scheme: http
  static_configs:
  - targets:
    - example.com:8000
```

## Analytics & visualization
### Grafana dashboard
Import `grafana-dashboard.json` or Grafana dashboard ID `123` for a demonstration of some of the metrics.

## Legal
Teamspeak is a registered trademark of Teamspeak Limited. This project is not endorsed by or affiliated with Teamspeak Limited in any way.

This project is licensed under the terms of the MIT license.

## Contact
* [GitHub](https://github.com/TilmannF)
* [Twitter](https://twitter.com/TilmannFelgner)