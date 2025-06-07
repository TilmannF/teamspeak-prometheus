FROM python:3.11-alpine

RUN \
  apk add --update git \
  && rm -rf /var/cache/apk/*

ADD app.py requirements.txt /

RUN pip install -r ./requirements.txt

EXPOSE 8000

CMD [ "python", "./app.py" ]