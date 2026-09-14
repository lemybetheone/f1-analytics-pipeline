# Extends the official Airflow image with this project's dependencies.
#
# They go in their OWN virtualenv, not Airflow's. The two dependency trees are
# incompatible: protobuf 6.33.6 against Airflow's 4.25.8, plus pydantic, click,
# typing_extensions and urllib3. Installing requirements.txt into Airflow's
# environment would break Airflow itself — and only at runtime, after a build
# that looked successful.
#
# The DAG calls this interpreter by full path rather than trusting whatever
# `python` happens to resolve to.

FROM apache/airflow:3.0.2

COPY requirements.txt /tmp/project-requirements.txt

RUN python -m venv /home/airflow/project-venv \
 && /home/airflow/project-venv/bin/pip install --no-cache-dir --upgrade pip \
 && /home/airflow/project-venv/bin/pip install --no-cache-dir \
      -r /tmp/project-requirements.txt