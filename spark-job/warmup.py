from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("aegis-warmup").getOrCreate()
spark.stop()
