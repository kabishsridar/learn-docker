import sys
import uuid
from python_on_whales import docker, DockerException

def get_container_name():
    # Use provided name or generate a random one
    if len(sys.argv) > 1:
        return sys.argv[1]
    return f"app-{str(uuid.uuid4())[:8]}"

def setup_infrastructure(base_name):
    # Configuration
    network_name = f"{base_name}-net"
    volume_name = f"{base_name}-vol"
    db_name = f"{base_name}-db"
    app_name = base_name

    print(f"--- Starting Infrastructure Setup for: {base_name} ---")

    try:
        # Phase 1: Network Setup
        if not docker.network.exists(network_name):
            print(f"Phase 1: Creating network: {network_name}")
            docker.network.create(network_name)

        # Phase 1: Volume Verification
        if not docker.volume.exists(volume_name):
            print(f"Phase 1: Creating volume: {volume_name}")
            docker.volume.create(volume_name)

        # Phase 2: Dependency Launch (Database)
        print("Phase 2: Launching Database...")
        if docker.container.exists(db_name):
            docker.container.remove(db_name, force=True)
            
            # creating database container
        db_container = docker.run(
            "postgres:15-alpine",
            name=db_name,
            detach=True,
            networks=[network_name],
            volumes=[(volume_name, "/var/lib/postgresql/data")],
            envs={"POSTGRES_PASSWORD": "password"}
        )

        # Phase 3: Application Launch
        print("Phase 3: Launching Application...")
        if docker.container.exists(app_name):
            docker.container.remove(app_name, force=True)

        app_container = docker.run(
            "nginx:latest", # Replace with your app image
            name=app_name,
            detach=True,
            networks=[network_name],
            publish=[(8080, 80)] # Maps host port 8080 to container port 80
        )


        print(f"--- Deployment Complete: {app_name} connected to {db_name} ---")

    except DockerException as e:
        print(f"An error occurred during deployment: {e}")
        sys.exit(1)

if __name__ == "__main__":
    name = get_container_name()
    setup_infrastructure(name)