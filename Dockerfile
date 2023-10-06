FROM docker.io/python:3
RUN apt update
RUN apt install ffmpeg gifsicle
RUN mkdir /workspace
RUN mkdir /workspace/downloads
ADD requirements.txt /workspace/
ADD run.py /workspace
ADD .conf.json /workspace
WORKDIR /workspace
RUN pip3 install -r requirements.txt
RUN pip3 install --upgrade sentry-sdk
CMD ["python3", "run.py"]
