FROM apache/spark:4.0.0

USER root

RUN pip install --no-cache-dir boto3

RUN curl -L -o ${SPARK_HOME}/jars/iceberg-spark-runtime-4.0_2.13-1.11.0.jar \
    https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-spark-runtime-4.0_2.13/1.11.0/iceberg-spark-runtime-4.0_2.13-1.11.0.jar \
    && curl -L -o ${SPARK_HOME}/jars/iceberg-aws-bundle-1.11.0.jar \
    https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-aws-bundle/1.11.0/iceberg-aws-bundle-1.11.0.jar

ENV SPARK_HOME=/opt/spark
ENV PATH="${SPARK_HOME}/bin:${SPARK_HOME}/sbin:${PATH}"

WORKDIR /workspace