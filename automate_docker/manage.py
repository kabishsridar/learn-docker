import os
import sys
from python_on_whales import docker, DockerException

# ANSI color escape codes for terminal coloring
BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"

def clear_screen():
    """Clears the terminal screen."""
    os.system('clear' if os.name == 'posix' else 'cls')

def print_header(title):
    """Prints a styled banner for sections."""
    print(f"\n{BOLD}{BLUE}=== {title} ==={RESET}\n")

def get_input(prompt, default=None):
    """Gets user input with an optional default value."""
    if default:
        res = input(f"{prompt} [{default}]: ").strip()
        return res if res else default
    return input(f"{prompt}: ").strip()

def format_ports(ports):
    """Formats the container port bindings dict into a readable string."""
    if not ports:
        return "None"
    mappings = []
    for container_port, host_bindings in ports.items():
        if host_bindings:
            for binding in host_bindings:
                host_ip = binding.get('HostIp', '0.0.0.0')
                host_port = binding.get('HostPort', '')
                mappings.append(f"{host_ip}:{host_port}->{container_port}")
        else:
            mappings.append(container_port)
    return ", ".join(mappings)

def scan_compose_dirs():
    """Scans workspace for directories containing docker-compose.yml."""
    compose_dirs = []
    for root, dirs, files in os.walk("."):
        # Skip hidden directories (ignoring current and parent dir markers)
        if any(part.startswith('.') for part in root.split(os.sep) if part not in ['.', '..']):
            continue
        if "docker-compose.yml" in files:
            compose_dirs.append(os.path.relpath(root, "."))
    return sorted(compose_dirs)

def get_used_host_ports():
    """Returns a set of all host ports currently in use by Docker containers."""
    used = set()
    try:
        for c in docker.container.list(all=True):
            if c.network_settings.ports:
                for cp, bindings in c.network_settings.ports.items():
                    if bindings:
                        for b in bindings:
                            hp = b.get('HostPort')
                            if hp:
                                used.add(int(hp))
    except Exception:
        pass
    return used

def get_free_port(start_port, used_ports):
    """Finds the first free host port starting from start_port."""
    port = start_port
    while port in used_ports:
        port += 1
    return port

def list_containers():
    """Lists all Docker containers on the system in a clean table format."""
    containers = docker.container.list(all=True)
    if not containers:
        print(f"{YELLOW}No containers found on the system.{RESET}")
        return []
    
    # Table header
    print(f"{BOLD}{'#':<3} {'ID':<12} {'NAME':<24} {'IMAGE':<24} {'STATUS':<15} {'PORTS':<20}{RESET}")
    print("-" * 105)
    for idx, c in enumerate(containers, 1):
        status_color = GREEN if c.state.status == "running" else RED
        ports_str = format_ports(c.network_settings.ports)
        
        # Truncate strings to prevent wrapping in basic terminal resolutions
        name = c.name[:23]
        image = c.config.image[:23]
        status = f"{status_color}{c.state.status}{RESET}"
        
        print(f"{idx:<3} {c.id[:12]:<12} {name:<24} {image:<24} {status:<15} {ports_str:<20}")
    print("-" * 105)
    return containers

def select_container_prompt(containers, action_name):
    """Prompts the user to pick a container from the listed index numbers."""
    if not containers:
        return None
    while True:
        choice = get_input(f"Enter the # of the container to {action_name} (or 0 to cancel)")
        try:
            idx = int(choice)
            if idx == 0:
                return None
            if 1 <= idx <= len(containers):
                return containers[idx - 1]
            print(f"{RED}Invalid selection. Choose a number between 1 and {len(containers)}.{RESET}")
        except ValueError:
            print(f"{RED}Please enter a valid number.{RESET}")

def create_container_wizard():
    """Interactive wizard to spin up a Docker Compose project and configure host ports."""
    print_header("CREATE NEW CONTAINER DEPLOYMENT (DOCKER COMPOSE)")
    
    dirs = scan_compose_dirs()
    if not dirs:
        print(f"{YELLOW}No directories with docker-compose.yml found in the workspace.{RESET}")
        custom_path = get_input("Enter the path to the folder containing docker-compose.yml (or Enter to cancel)")
        if not custom_path or not os.path.exists(custom_path):
            print(f"{RED}Invalid path. Cancelled.{RESET}")
            return
        selected_dir = custom_path
    else:
        print("Found directories with docker-compose.yml:")
        for idx, d in enumerate(dirs, 1):
            print(f"  [{idx}] {d}")
        print("  [0] Specify a custom directory path")
        
        choice = get_input("Select a directory", "1")
        try:
            choice_idx = int(choice)
            if choice_idx == 0:
                custom_path = get_input("Enter custom directory path")
                if not custom_path or not os.path.exists(custom_path):
                    print(f"{RED}Invalid path. Cancelled.{RESET}")
                    return
                selected_dir = custom_path
            elif 1 <= choice_idx <= len(dirs):
                selected_dir = dirs[choice_idx - 1]
            else:
                print(f"{RED}Invalid choice. Cancelled.{RESET}")
                return
        except ValueError:
            print(f"{RED}Invalid input. Cancelled.{RESET}")
            return
            
    compose_path = os.path.join(selected_dir, "docker-compose.yml")
    if not os.path.exists(compose_path):
        print(f"{RED}Could not find docker-compose.yml in '{selected_dir}'.{RESET}")
        return
        
    print(f"{GREEN}Selected compose file: {compose_path}{RESET}")
    
    # Parse docker-compose.yml to find service ports to override
    try:
        import yaml
        with open(compose_path, 'r') as f:
            data = yaml.safe_load(f)
    except Exception as e:
        print(f"{RED}Failed to parse docker-compose.yml: {e}. Executing docker-compose up directly...{RESET}")
        try:
            from python_on_whales import DockerClient
            client = DockerClient(compose_files=[os.path.abspath(compose_path)])
            client.compose.up(detach=True)
            print(f"{GREEN}Successfully launched docker compose in '{selected_dir}'!{RESET}")
        except Exception as ce:
            print(f"{RED}Failed to run docker compose: {ce}{RESET}")
        return
        
    services = data.get("services", {})
    candidates = []
    for s_name, srv in services.items():
        ports = srv.get("ports", [])
        if ports:
            is_db = any(x in s_name.lower() for x in ["db", "database", "postgres", "mysql", "redis", "mongo"])
            for p in ports:
                if isinstance(p, str) and (":5432" in p or ":3306" in p or ":6379" in p):
                    is_db = True
            candidates.append((s_name, ports, is_db))
            
    # Exclude DB candidate services if there are non-DB ones
    non_db_candidates = [c for c in candidates if not c[2]]
    target_candidates = non_db_candidates if non_db_candidates else candidates
    
    override_ports = {}
    used_ports = get_used_host_ports()
    
    for s_name, ports, is_db in target_candidates:
        print(f"\nService '{s_name}' has the following default port mappings:")
        new_service_ports = []
        for p in ports:
            if isinstance(p, str) and ":" in p:
                host_port_str, container_port_str = p.split(":")
                try:
                    host_port = int(host_port_str)
                    container_port = int(container_port_str)
                except ValueError:
                    new_service_ports.append(p)
                    continue
                    
                suggested = get_free_port(host_port, used_ports)
                ans = get_input(f"Enter host port for container port {container_port} (service: {s_name})", str(suggested))
                try:
                    selected_port = int(ans)
                    new_service_ports.append(f"{selected_port}:{container_port}")
                    used_ports.add(selected_port)
                except ValueError:
                    print(f"{YELLOW}Invalid port input. Keeping default '{p}'.{RESET}")
                    new_service_ports.append(p)
            else:
                new_service_ports.append(p)
        override_ports[s_name] = new_service_ports
        
    override_path = None
    if override_ports:
        override_data = {
            "version": data.get("version", "3.8"),
            "services": {
                s_name: {"ports": ports_list} for s_name, ports_list in override_ports.items()
            }
        }
        override_path = os.path.join(selected_dir, "docker-compose.override.yml")
        
        if os.path.exists(override_path):
            bak_path = override_path + ".bak"
            print(f"{YELLOW}Backing up existing docker-compose.override.yml to {bak_path}{RESET}")
            try:
                os.replace(override_path, bak_path)
            except Exception as oe:
                print(f"{RED}Warning: Could not backup override file: {oe}{RESET}")
                
        try:
            with open(override_path, 'w') as f:
                yaml.dump(override_data, f)
            print(f"{GREEN}Created override config: {override_path}{RESET}")
        except Exception as we:
            print(f"{RED}Failed to write override file: {we}. Continuing with default config...{RESET}")
            override_path = None
            
    try:
        print(f"{CYAN}Running docker compose up...{RESET}")
        from python_on_whales import DockerClient
        compose_files = [os.path.abspath(compose_path)]
        if override_path and os.path.exists(override_path):
            compose_files.append(os.path.abspath(override_path))
            
        client = DockerClient(compose_files=compose_files)
        client.compose.up(detach=True)
        print(f"\n{GREEN}Successfully deployed docker compose services in '{selected_dir}'!{RESET}")
    except Exception as ce:
        print(f"{RED}Failed to run docker compose: {ce}{RESET}")

def power_control_action(containers):
    """Controls container execution states: start, stop, restart."""
    print_header("START / STOP / RESTART CONTAINER")
    c = select_container_prompt(containers, "control")
    if not c:
        return
    
    print(f"\nSelected container: {BOLD}{c.name}{RESET} ({c.id[:12]}) - Status: {c.state.status}")
    print("Actions:")
    print("  [1] Start")
    print("  [2] Stop")
    print("  [3] Restart")
    print("  [0] Cancel")
    
    action = get_input("Select action", "1")
    try:
        if action == "1":
            print(f"Starting container '{c.name}'...")
            c.start()
            print(f"{GREEN}Started successfully.{RESET}")
        elif action == "2":
            print(f"Stopping container '{c.name}'...")
            c.stop()
            print(f"{GREEN}Stopped successfully.{RESET}")
        elif action == "3":
            print(f"Restarting container '{c.name}'...")
            c.restart()
            print(f"{GREEN}Restarted successfully.{RESET}")
        else:
            print("Cancelled.")
    except DockerException as e:
        print(f"{RED}Operation failed: {e}{RESET}")

def remove_container_action(containers):
    """Deletes selected container."""
    print_header("REMOVE CONTAINER")
    c = select_container_prompt(containers, "remove")
    if not c:
        return
    
    confirm = get_input(f"Are you sure you want to permanently remove container '{c.name}'? (y/n)", "n").lower()
    if confirm != 'y':
        print("Cancelled.")
        return
        
    force = get_input("Force remove? (Stops container if running) (y/n)", "n").lower()
    try:
        print(f"Removing container '{c.name}'...")
        c.remove(force=(force == 'y'))
        print(f"{GREEN}Container removed successfully.{RESET}")
    except DockerException as e:
        print(f"{RED}Failed to remove container: {e}{RESET}")

def rename_container_action(containers):
    """Renames a selected container."""
    print_header("RENAME CONTAINER")
    c = select_container_prompt(containers, "rename")
    if not c:
        return
    
    print(f"\nCurrent name: {BOLD}{c.name}{RESET}")
    new_name = get_input("Enter new name for the container")
    if not new_name:
        print(f"{RED}New name cannot be empty.{RESET}")
        return
        
    try:
        print(f"Renaming container '{c.name}' to '{new_name}'...")
        docker.container.rename(c, new_name)
        print(f"{GREEN}Container renamed successfully.{RESET}")
    except DockerException as e:
        print(f"{RED}Failed to rename container: {e}{RESET}")

def logs_action(containers):
    """Displays or streams logs for a container."""
    print_header("VIEW CONTAINER LOGS")
    c = select_container_prompt(containers, "view logs")
    if not c:
        return
    
    print("\nLog Options:")
    print("  [1] View last 100 lines")
    print("  [2] Stream logs (Ctrl+C to stop)")
    choice = get_input("Select option", "1")
    
    try:
        if choice == "1":
            logs = docker.container.logs(c, tail=100)
            print(f"\n{BOLD}=== Logs for {c.name} (Last 100 lines) ==={RESET}\n")
            print(logs)
            print(f"\n{BOLD}=== End of Logs ==={RESET}\n")
        elif choice == "2":
            print(f"\n{BOLD}=== Streaming logs for {c.name} (Press Ctrl+C to stop) ==={RESET}\n")
            try:
                for stream_type, content in docker.container.logs(c, stream=True):
                    sys.stdout.write(content.decode('utf-8', errors='replace'))
                    sys.stdout.flush()
            except KeyboardInterrupt:
                print(f"\n{YELLOW}Stopped streaming logs.{RESET}")
        else:
            print("Invalid option.")
    except DockerException as e:
        print(f"{RED}Failed to read logs: {e}{RESET}")

def list_images():
    """Lists all Docker images on the system in a clean table format."""
    print_header("DOCKER IMAGES")
    try:
        images = docker.image.list()
        if not images:
            print(f"{YELLOW}No images found on the system.{RESET}")
            return []
        
        print(f"{BOLD}{'#':<3} {'ID':<12} {'REPOSITORY:TAG':<50} {'SIZE':<12}{RESET}")
        print("-" * 80)
        for idx, img in enumerate(images, 1):
            tags_str = ", ".join(img.repo_tags) if img.repo_tags else "<none>:<none>"
            img_id = img.id.replace("sha256:", "")[:12]
            size_mb = img.size / (1024**2)
            print(f"{idx:<3} {img_id:<12} {tags_str:<50} {size_mb:.2f} MB")
        print("-" * 80)
        return images
    except DockerException as e:
        print(f"{RED}Failed to list images: {e}{RESET}")
        return []

def remove_image_action():
    """Deletes a selected Docker image."""
    images = list_images()
    if not images:
        return
    print_header("REMOVE DOCKER IMAGE")
    while True:
        choice = get_input("Enter the # of the image to remove (or 0 to cancel)")
        try:
            idx = int(choice)
            if idx == 0:
                return
            if 1 <= idx <= len(images):
                img = images[idx - 1]
                break
            print(f"{RED}Invalid selection. Choose between 1 and {len(images)}.{RESET}")
        except ValueError:
            print(f"{RED}Please enter a valid number.{RESET}")
            
    tags_str = ", ".join(img.repo_tags) if img.repo_tags else img.id[:12]
    confirm = get_input(f"Are you sure you want to permanently remove image '{tags_str}'? (y/n)", "n").lower()
    if confirm != 'y':
        print("Cancelled.")
        return
        
    force = get_input("Force remove? (y/n)", "n").lower()
    try:
        print(f"Removing image '{tags_str}'...")
        docker.image.remove(img, force=(force == 'y'))
        print(f"{GREEN}Image removed successfully.{RESET}")
    except DockerException as e:
        print(f"{RED}Failed to remove image: {e}{RESET}")

def rename_image_action():
    """Adds a new tag to an image, optionally untagging the old name (effectively renaming)."""
    images = list_images()
    if not images:
        return
    print_header("RENAME (TAG/RETAG) DOCKER IMAGE")
    while True:
        choice = get_input("Enter the # of the image to rename/tag (or 0 to cancel)")
        try:
            idx = int(choice)
            if idx == 0:
                return
            if 1 <= idx <= len(images):
                img = images[idx - 1]
                break
            print(f"{RED}Invalid selection. Choose between 1 and {len(images)}.{RESET}")
        except ValueError:
            print(f"{RED}Please enter a valid number.{RESET}")
            
    tags_str = ", ".join(img.repo_tags) if img.repo_tags else img.id[:12]
    print(f"\nSelected image: {BOLD}{tags_str}{RESET}")
    new_tag = get_input("Enter new tag name (e.g., my-image:v2)")
    if not new_tag:
        print(f"{RED}New tag cannot be empty.{RESET}")
        return
        
    try:
        print(f"Tagging image with '{new_tag}'...")
        docker.image.tag(img, new_tag)
        print(f"{GREEN}Image tagged successfully.{RESET}")
        
        if img.repo_tags:
            remove_old = get_input("Do you want to untag/remove the old names? (y/n)", "n").lower()
            if remove_old == 'y':
                for old_tag in img.repo_tags:
                    if old_tag != new_tag:
                        print(f"Untagging '{old_tag}'...")
                        try:
                            docker.image.remove(old_tag)
                        except DockerException as ue:
                            print(f"{YELLOW}Could not untag '{old_tag}': {ue}{RESET}")
                print(f"{GREEN}Old tags removed.{RESET}")
    except DockerException as e:
        print(f"{RED}Failed to tag/rename image: {e}{RESET}")

def change_port_action(containers):
    """Changes the port mapping of a container by stopping, removing, and recreating it."""
    print_header("CHANGE CONTAINER PORT MAPPING")
    c = select_container_prompt(containers, "change port mapping")
    if not c:
        return
        
    mapped_ports = []
    if c.network_settings.ports:
        for cp_str, bindings in c.network_settings.ports.items():
            cp = int(cp_str.split('/')[0])
            if bindings:
                for b in bindings:
                    hp = b.get('HostPort')
                    if hp:
                        mapped_ports.append((int(hp), cp))
                        
    if not mapped_ports:
        print(f"{YELLOW}Container '{c.name}' has no active host port mappings.{RESET}")
        container_port_str = get_input("Enter container port to map (e.g. 80)")
        try:
            container_port = int(container_port_str)
        except ValueError:
            print(f"{RED}Invalid container port. Cancelled.{RESET}")
            return
        old_host_port = None
    else:
        if len(mapped_ports) > 1:
            print("Current port mappings:")
            for idx, (hp, cp) in enumerate(mapped_ports, 1):
                print(f"  [{idx}] Host {hp} -> Container {cp}")
            p_choice = get_input("Select mapping to change", "1")
            try:
                p_idx = int(p_choice) - 1
                if 0 <= p_idx < len(mapped_ports):
                    old_host_port, container_port = mapped_ports[p_idx]
                else:
                    print(f"{RED}Invalid choice. Cancelled.{RESET}")
                    return
            except ValueError:
                print(f"{RED}Invalid choice. Cancelled.{RESET}")
                return
        else:
            old_host_port, container_port = mapped_ports[0]
            
    print(f"\nCurrent mapping: Host {old_host_port if old_host_port else 'None'} -> Container {container_port}")
    
    used_ports = get_used_host_ports()
    suggested = get_free_port(old_host_port if old_host_port else 8080, used_ports)
    
    ans = get_input(f"Enter new host port for container port {container_port}", str(suggested))
    try:
        new_host_port = int(ans)
    except ValueError:
        print(f"{RED}Invalid port. Cancelled.{RESET}")
        return
        
    if new_host_port in used_ports and new_host_port != old_host_port:
        print(f"{RED}Port {new_host_port} is already in use by another container!{RESET}")
        confirm = get_input("Do you want to proceed anyway? (y/n)", "n").lower()
        if confirm != 'y':
            return

    try:
        envs = {}
        for item in c.config.env:
            if '=' in item:
                k, v = item.split('=', 1)
                envs[k] = v
                
        volumes = []
        for m in c.mounts:
            if m.type == 'volume':
                volumes.append((m.name, m.destination))
            elif m.type == 'bind':
                volumes.append((m.source, m.destination))
                
        networks = list(c.network_settings.networks.keys())
        first_network = networks[0] if networks else None
        additional_networks = networks[1:] if len(networks) > 1 else []
        
        restart_policy = c.host_config.restart_policy.name
        if not restart_policy:
            restart_policy = "no"
            
        publish = []
        if c.network_settings.ports:
            for cp_str, bindings in c.network_settings.ports.items():
                cp = int(cp_str.split('/')[0])
                if cp == container_port:
                    publish.append((new_host_port, container_port))
                else:
                    if bindings:
                        for b in bindings:
                            hp = b.get('HostPort')
                            if hp:
                                publish.append((int(hp), cp))
        else:
            publish.append((new_host_port, container_port))
            
        if (new_host_port, container_port) not in publish:
            publish.append((new_host_port, container_port))
            
        image = c.config.image
        name = c.name
        
        print(f"\n{CYAN}Recreation process will stop and remove container '{name}'...{RESET}")
        confirm_recreate = get_input("Proceed? (y/n)", "y").lower()
        if confirm_recreate != 'y':
            print("Cancelled.")
            return
            
        print(f"Stopping container '{name}'...")
        c.stop()
        print(f"Removing container '{name}'...")
        c.remove()
        
        print(f"Launching container '{name}' with new port mapping...")
        new_c = docker.run(
            image,
            name=name,
            detach=True,
            publish=publish,
            envs=envs if envs else None,
            volumes=volumes if volumes else None,
            networks=[first_network] if first_network else None,
            restart=restart_policy
        )
        
        for net in additional_networks:
            try:
                docker.network.connect(net, new_c)
            except Exception as ne:
                print(f"{YELLOW}Warning: Could not reconnect to network '{net}': {ne}{RESET}")
                
        print(f"{GREEN}Success! Container '{name}' port changed. Host {new_host_port} -> Container {container_port}.{RESET}")
    except Exception as e:
        print(f"{RED}Failed to change port mapping: {e}{RESET}")

def system_info_action():
    """Displays server info and resource statistics."""
    print_header("DOCKER SYSTEM INFO")
    try:
        info = docker.system.info()
        print(f"{BOLD}Docker Host:{RESET}     {info.name}")
        print(f"{BOLD}Server Version:{RESET}  {info.server_version}")
        print(f"{BOLD}OS / Kernel:{RESET}     {info.operating_system} / {info.kernel_version}")
        print(f"{BOLD}CPUs / Memory:{RESET}   {info.n_cpu} cores / {info.mem_total / (1024**3):.2f} GB")
        print(f"{BOLD}Containers:{RESET}      Total: {info.containers} (Running: {info.containers_running}, Stopped: {info.containers_stopped})")
        
        # Calculate other resources
        num_images = len(docker.image.list())
        num_networks = len(docker.network.list())
        num_volumes = len(docker.volume.list())
        
        print(f"{BOLD}Images Count:{RESET}    {num_images}")
        print(f"{BOLD}Networks Count:{RESET}  {num_networks}")
        print(f"{BOLD}Volumes Count:{RESET}   {num_volumes}")
        
        df = docker.system.disk_free()
        print(f"\n{BOLD}Docker Disk Usage:{RESET}")
        print(f"  Images:     size={df.images.size / (1024**2):.2f} MB, active={df.images.active}, reclaimable={df.images.reclaimable / (1024**2):.2f} MB (total: {df.images.total_count})")
        print(f"  Containers: size={df.containers.size / (1024**2):.2f} MB, active={df.containers.active}, reclaimable={df.containers.reclaimable / (1024**2):.2f} MB (total: {df.containers.total_count})")
        print(f"  Volumes:    size={df.volumes.size / (1024**2):.2f} MB, active={df.volumes.active}, reclaimable={df.volumes.reclaimable / (1024**2):.2f} MB (total: {df.volumes.total_count})")
        
    except Exception as e:
        print(f"{RED}Failed to retrieve system info: {e}{RESET}")

def main():
    while True:
        clear_screen()
        print(f"{BOLD}{BLUE}==========================================================================")
        print(f"                        DOCKER INTERACTIVE MANAGER                        ")
        print(f"=========================================================================={RESET}")
        
        containers = []
        try:
            containers = list_containers()
        except DockerException as e:
            print(f"{RED}Error connecting to Docker daemon: {e}{RESET}")
            print("Please ensure Docker is running and you have access permissions.")
            sys.exit(1)
            
        print("\nOptions:")
        print("  [1] Refresh list")
        print("  [2] Create a new container deployment (Compose)")
        print("  [3] Start / Stop / Restart a container")
        print("  [4] Remove a container")
        print("  [5] Rename a container")
        print("  [6] View container logs")
        print("  [7] Remove a Docker image")
        print("  [8] Rename (Tag/Retag) a Docker image")
        print("  [9] Change a container's port mapping")
        print("  [10] Docker System Info")
        print("  [11] Exit")
        
        choice = get_input("\nEnter option", "1")
        
        if choice == "1":
            continue
        elif choice == "2":
            create_container_wizard()
        elif choice == "3":
            power_control_action(containers)
        elif choice == "4":
            remove_container_action(containers)
        elif choice == "5":
            rename_container_action(containers)
        elif choice == "6":
            logs_action(containers)
        elif choice == "7":
            remove_image_action()
        elif choice == "8":
            rename_image_action()
        elif choice == "9":
            change_port_action(containers)
        elif choice == "10":
            system_info_action()
        elif choice == "11":
            print(f"\n{GREEN}Goodbye!{RESET}")
            break
        else:
            print(f"{RED}Invalid option!{RESET}")
            
        input(f"\nPress Enter to return to main menu...")

if __name__ == "__main__":
    main()
