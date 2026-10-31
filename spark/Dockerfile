FROM apache/spark:4.0.0

USER root

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

RUN curl -L -o ${SPARK_HOME}/jars/iceberg-spark-runtime-4.0_2.13-1.11.0.jar \
    https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-spark-runtime-4.0_2.13/1.11.0/iceberg-spark-runtime-4.0_2.13-1.11.0.jar \
    && curl -L -o ${SPARK_HOME}/jars/iceberg-aws-bundle-1.11.0.jar \
    https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-aws-bundle/1.11.0/iceberg-aws-bundle-1.11.0.jar \
    && curl -L -o ${SPARK_HOME}/jars/hadoop-aws-3.4.1.jar \
    https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-aws/3.4.1/hadoop-aws-3.4.1.jar \
    && curl -L -o ${SPARK_HOME}/jars/hadoop-common-3.4.1.jar \
    https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-common/3.4.1/hadoop-common-3.4.1.jar \
    && curl -L -o ${SPARK_HOME}/jars/bundle-2.24.6.jar \
    https://repo1.maven.org/maven2/software/amazon/awssdk/bundle/2.24.6/bundle-2.24.6.jar

ENV SPARK_HOME=/opt/spark
ENV PATH="${SPARK_HOME}/bin:${SPARK_HOME}/sbin:${PATH}"

WORKDIR /workspace

COPY spark_config.py /workspace/jobs/spark_config.py
COPY spark_runner.py /workspace/jobs/spark_runner.py