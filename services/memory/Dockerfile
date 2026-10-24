FROM docker.io/supermemory/supermemory:0.0.9

# Railway volumes mount root-owned. The image user cannot create the store there.
USER root
ENTRYPOINT ["sh", "-c", "mkdir -p /data && chmod 777 /data && exec supermemory-server"]
