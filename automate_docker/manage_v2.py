import os
import sys
import json
import yaml
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
    if default is not None:
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
    script_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_root = os.path.dirname(script_dir)
    
    compose_dirs = []
    for root, dirs, files in os.walk(workspace_root):
        # Skip hidden directories (ignoring current and parent dir markers)
        if any(part.startswith('.') for part in root.split(os.sep) if part not in ['.', '..']):
            continue
        if "docker-compose.yml" in files:
            compose_dirs.append(os.path.abspath(root))
    return sorted(compose_dirs)


# ---------------------------------------------------------------------------
# Persistent manager config  (stored alongside this script)
# ---------------------------------------------------------------------------
_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".docker_manager.json")

def load_manager_config():
    """Loads the persistent manager configuration from .docker_manager.json.
    Returns a dict with keys like 'auto_replicate_projects' (list of abs paths)."""
    if os.path.exists(_CONFIG_FILE):
        try:
            with open(_CONFIG_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {"auto_replicate_projects": []}

def save_manager_config(config):
    """Saves the manager configuration dict to .docker_manager.json."""
    try:
        with open(_CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        print(f"{RED}Warning: Could not save manager config: {e}{RESET}")
        return False

def is_auto_replicate_enabled(project_dir):
    """Returns True if the given project directory has auto-replication enabled."""
    cfg = load_manager_config()
    return os.path.abspath(project_dir) in [os.path.abspath(p) for p in cfg.get("auto_replicate_projects", [])]

def get_container_ports(c):
    """Retrieves port bindings for a container, handling both running and stopped states."""
    try:
        has_active_ports = False
        if c.network_settings.ports:
            for val in c.network_settings.ports.values():
                if val:
                    has_active_ports = True
                    break
        if has_active_ports:
            return format_ports(c.network_settings.ports)
        if c.host_config.port_bindings:
            mappings = []
            for container_port, host_bindings in c.host_config.port_bindings.items():
                if host_bindings:
                    for binding in host_bindings:
                        host_ip = getattr(binding, 'host_ip', '0.0.0.0') or '0.0.0.0'
                        host_port = getattr(binding, 'host_port', '')
                        mappings.append(f"{host_ip}:{host_port}->{container_port}")
                else:
                    mappings.append(container_port)
            return ", ".join(mappings)
    except Exception:
        pass
    return "None"

def get_used_host_ports():
    """Returns a set of all host ports currently configured in use by all Docker containers."""
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
            if c.host_config.port_bindings:
                for cp, bindings in c.host_config.port_bindings.items():
                    if bindings:
                        for b in bindings:
                            hp = getattr(b, 'host_port', None)
                            if hp:
                                try:
                                    used.add(int(hp))
                                except ValueError:
                                    pass
    except Exception:
        pass
    return used

import socket

def is_host_port_free(port):
    """Checks if a port is free on the host machine using socket binding."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('0.0.0.0', port))
            return True
    except OSError:
        return False

def get_free_port(start_port, used_ports):
    """Finds the first free host port starting from start_port, checking Docker and Host OS."""
    port = start_port
    while port in used_ports or not is_host_port_free(port):
        port += 1
    return port

def detect_port_conflicts(containers):
    """Scans all containers (running or stopped) and returns a dict mapping
    container.id -> list of warning strings for any host ports that are shared
    with another container (clash) or would conflict if the stopped container starts."""
    # Build map: host_port -> list of (container_id, container_name, status)
    port_owners = {}  # host_port (int) -> list of (id, name, status)
    for c in containers:
        try:
            # Collect all configured host ports (running or stopped)
            conf_ports = set()
            if c.network_settings and c.network_settings.ports:
                for cp, bindings in c.network_settings.ports.items():
                    if bindings:
                        for b in bindings:
                            hp = b.get('HostPort')
                            if hp:
                                try:
                                    conf_ports.add(int(hp))
                                except ValueError:
                                    pass
            if c.host_config and c.host_config.port_bindings:
                for cp, bindings in c.host_config.port_bindings.items():
                    if bindings:
                        for b in bindings:
                            hp = getattr(b, 'host_port', None)
                            if hp:
                                try:
                                    conf_ports.add(int(hp))
                                except ValueError:
                                    pass
            for port in conf_ports:
                port_owners.setdefault(port, []).append((c.id, c.name, c.state.status))
        except Exception:
            continue

    # Build per-container warning map
    conflicts = {}  # container_id -> list of warning strings
    for port, owners in port_owners.items():
        if len(owners) < 2:
            continue
        running_owners = [o for o in owners if o[2] == "running"]
        stopped_owners = [o for o in owners if o[2] != "running"]
        for cid, cname, cstatus in owners:
            warnings_list = conflicts.setdefault(cid, [])
            others = [o for o in owners if o[0] != cid]
            for ocid, oname, ostatus in others:
                if cstatus == "running" and ostatus == "running":
                    warnings_list.append(f"⚠ PORT CLASH on :{port} with '{oname}' (both running!)")
                elif cstatus == "running" and ostatus != "running":
                    warnings_list.append(f"⚠ STOPPED CLASH on :{port} — '{oname}' is stopped but would conflict if started")
                elif cstatus != "running" and ostatus == "running":
                    warnings_list.append(f"⚠ PORT TAKEN on :{port} — '{oname}' is already using this port (running)")
                else:
                    warnings_list.append(f"⚠ PORT OVERLAP on :{port} with '{oname}' (both stopped, will clash if both start)")
    return conflicts


def list_containers():
    """Lists all Docker containers on the system in a clean table format.
    Includes a WARNING column if any host port conflicts are detected."""
    containers = docker.container.list(all=True)
    if not containers:
        print(f"{YELLOW}No containers found on the system.{RESET}")
        return []

    # Detect port conflicts across all containers
    conflict_map = detect_port_conflicts(containers)

    # Table header
    print(f"{BOLD}{'#':<3} {'ID':<12} {'NAME':<24} {'IMAGE':<24} {'STATUS':<15} {'PORTS':<25} {'WARNING'}{RESET}")
    print("-" * 120)
    for idx, c in enumerate(containers, 1):
        try:
            status_val = c.state.status
            status_color = GREEN if status_val == "running" else RED
            ports_str = get_container_ports(c)

            # Truncate strings to prevent wrapping in basic terminal resolutions
            name = c.name[:23]
            image = c.config.image[:23]
            status = f"{status_color}{status_val}{RESET}"

            # Build warning tag from conflict map
            warnings_list = conflict_map.get(c.id, [])
            if warnings_list:
                # Show a compact inline tag; full details appear in the alert section
                warn_tag = f"{YELLOW}⚠ PORT CONFLICT{RESET}"
            else:
                warn_tag = ""

            print(f"{idx:<3} {c.id[:12]:<12} {name:<24} {image:<24} {status:<15} {ports_str:<25} {warn_tag}")
        except Exception:
            # Skip container if it is removed or in transition
            continue
    print("-" * 120)
    return containers

def select_containers_prompt(containers, action_name):
    """Prompts the user to pick one or more containers (separated by spaces or commas) from the listed index numbers."""
    if not containers:
        return []
    while True:
        choice = get_input(f"Enter the # of the container(s) to {action_name} (separated by spaces/commas, or 0 to cancel)")
        if not choice:
            continue
        
        # Replace commas with spaces to handle both separators
        normalized = choice.replace(',', ' ').strip()
        parts = normalized.split()
        
        # If user explicitly entered "0", return empty list to cancel
        if len(parts) == 1 and parts[0] == "0":
            return []
            
        selected = []
        valid = True
        for part in parts:
            try:
                idx = int(part)
                if idx == 0:
                    continue
                if 1 <= idx <= len(containers):
                    c = containers[idx - 1]
                    if c not in selected:
                        selected.append(c)
                else:
                    print(f"{RED}Invalid selection: {part}. Choose a number between 1 and {len(containers)}.{RESET}")
                    valid = False
                    break
            except ValueError:
                print(f"{RED}Please enter valid number(s) (e.g. 2 6 12).{RESET}")
                valid = False
                break
        
        if valid and selected:
            return selected
        elif not valid:
            continue
        else:
            return []

def duplicate_volumes(volumes_list, replica_suffix="_replica_2"):
    """Suffixes the named volumes or bind mounts for replica containers to isolate write actions."""
    if not volumes_list:
        return [], []
    new_volumes = []
    named_volumes_to_declare = []
    for vol in volumes_list:
        if isinstance(vol, str):
            parts = vol.split(':')
            if len(parts) >= 2:
                src, dest = parts[0], parts[1]
                opts = parts[2:]
                # Check if src is a path or named volume
                if src.startswith('.') or src.startswith('/') or src.startswith('~') or '/' in src or '\\' in src:
                    # Host path bind mount. Suffix the directory/file name
                    src_suffix = src + replica_suffix
                    new_vol = f"{src_suffix}:{dest}"
                    if opts:
                        new_vol += ":" + ":".join(opts)
                    new_volumes.append(new_vol)
                else:
                    # Named volume. Suffix the volume name
                    src_suffix = src + replica_suffix
                    new_vol = f"{src_suffix}:{dest}"
                    if opts:
                        new_vol += ":" + ":".join(opts)
                    new_volumes.append(new_vol)
                    named_volumes_to_declare.append(src_suffix)
            else:
                # Anonymous volume (just container path)
                new_volumes.append(vol)
        elif isinstance(vol, dict):
            new_vol = vol.copy()
            src = vol.get('source')
            vol_type = vol.get('type', 'volume')
            if src:
                if vol_type == 'bind' or src.startswith('.') or src.startswith('/') or src.startswith('~') or '/' in src or '\\' in src:
                    new_vol['source'] = src + replica_suffix
                else:
                    new_vol['source'] = src + replica_suffix
                    named_volumes_to_declare.append(src + replica_suffix)
            new_volumes.append(new_vol)
        else:
            new_volumes.append(vol)
    return new_volumes, named_volumes_to_declare

def check_failover_alerts(containers):
    """Checks running containers for replicated services where one of the copies has failed.
    Also identifies failed unreplicated services, partial project failures, and port conflicts.
    Returns (alerts, unreplicated_failed)."""
    alerts = []
    unreplicated_failed = []
    if not containers:
        return alerts, unreplicated_failed

    # --- Port-conflict alerts (cross-container host-port clashes) ---
    conflict_map = detect_port_conflicts(containers)
    # Emit one combined alert per unique (port, pair) combination (deduplicate)
    seen_clash_pairs = set()
    for c in containers:
        cid = c.id
        if cid not in conflict_map:
            continue
        try:
            cname = c.name
            cstatus = c.state.status
        except Exception:
            continue
        for warning_msg in conflict_map[cid]:
            # Build a stable key for the pair to avoid duplicates (A↔B = B↔A)
            # Extract the other container name from the message
            pair_key = tuple(sorted([cid, warning_msg]))
            if pair_key in seen_clash_pairs:
                continue
            seen_clash_pairs.add(pair_key)
            if "both running" in warning_msg:
                alerts.append(
                    f"{RED}[PORT CLASH] Container '{cname}' is RUNNING but shares a host port with another "
                    f"running container!\n"
                    f"        → {warning_msg}\n"
                    f"        Stop one of them or change its port mapping (Option 9) to prevent crashes.{RESET}"
                )
            elif "PORT TAKEN" in warning_msg:
                alerts.append(
                    f"{YELLOW}[PORT CONFLICT] Container '{cname}' (currently STOPPED) has a port that is "
                    f"already in use by a running container.\n"
                    f"        → {warning_msg}\n"
                    f"        Do NOT start '{cname}' — change its port first (Option 9) to avoid a crash.{RESET}"
                )
            elif "STOPPED CLASH" in warning_msg:
                alerts.append(
                    f"{YELLOW}[PORT WARNING] Container '{cname}' (RUNNING) shares a port with a stopped "
                    f"container.\n"
                    f"        → {warning_msg}\n"
                    f"        Change the stopped container's port (Option 9) before starting it.{RESET}"
                )
            elif "OVERLAP" in warning_msg:
                alerts.append(
                    f"{YELLOW}[PORT OVERLAP] Two stopped containers share the same host port.\n"
                    f"        → '{cname}': {warning_msg}\n"
                    f"        Resolve the conflict before starting both.{RESET}"
                )

    # --- Group containers by project name ---
    project_containers = {}
    for c in containers:
        try:
            labels = c.config.labels
            proj = labels.get("com.docker.compose.project")
            if proj:
                project_containers.setdefault(proj, []).append(c)
        except Exception:
            continue

    for proj, c_list in project_containers.items():
        # Check if at least one container in the project is currently running
        proj_has_running = False
        for c in c_list:
            try:
                if c.state.status == "running":
                    proj_has_running = True
                    break
            except Exception:
                continue
        if not proj_has_running:
            continue

        # Map service name to container object (preferring running ones if multiple exist)
        service_map = {}
        for c in c_list:
            try:
                svc = c.config.labels.get("com.docker.compose.service")
                if svc:
                    if svc not in service_map:
                        service_map[svc] = c
                    else:
                        existing_c = service_map[svc]
                        if existing_c.state.status != "running" and c.state.status == "running":
                            service_map[svc] = c
            except Exception:
                continue

        # --- Partial project failure warning ---
        # Identify non-replica primary services that are down while others in the same project run
        down_services = []
        running_services = []
        for svc_name, c in service_map.items():
            if svc_name.endswith("_replica_2"):
                continue
            try:
                if c.state.status == "running":
                    running_services.append((svc_name, get_container_ports(c)))
                else:
                    down_services.append((svc_name, get_container_ports(c)))
            except Exception:
                continue

        if down_services and running_services:
            down_lines = ""
            for svc_name, ports in down_services:
                replica_name = f"{svc_name}_replica_2"
                replica_info = ""
                if replica_name in service_map:
                    try:
                        replica_c = service_map[replica_name]
                        if replica_c.state.status == "running":
                            replica_ports = get_container_ports(replica_c)
                            replica_info = f" → Use backup '{replica_name}' on port {replica_ports} instead"
                        else:
                            replica_info = f" → No running backup exists!"
                    except Exception:
                        pass
                down_lines += f"\n          • '{svc_name}' (configured port: {ports}){replica_info}"
            running_lines = ", ".join(f"'{s}'" for s, _ in running_services)
            alerts.append(
                f"{YELLOW}[⚠ PARTIAL FAILURE] Project '{proj}' is PARTIALLY running — "
                f"only {running_lines} {'is' if len(running_services) == 1 else 'are'} up.\n"
                f"        Down services:{down_lines}{RESET}"
            )

        for svc_name, c in service_map.items():
            try:
                # Skip replica containers in primary checks
                if svc_name.endswith("_replica_2"):
                    continue

                replica_name = f"{svc_name}_replica_2"
                if replica_name in service_map:
                    primary_c = c
                    replica_c = service_map[replica_name]

                    primary_running = primary_c.state.status == "running"
                    replica_running = replica_c.state.status == "running"

                    primary_ports = get_container_ports(primary_c)
                    replica_ports = get_container_ports(replica_c)

                    if not primary_running and replica_running:
                        alerts.append(
                            f"{YELLOW}[ALERT] Primary service '{svc_name}' (port {primary_ports}) in project '{proj}' is DOWN!\n"
                            f"        ✅ Use the healthy backup copy '{replica_name}' on port {replica_ports} instead.{RESET}"
                        )
                    elif primary_running and not replica_running:
                        alerts.append(
                            f"{YELLOW}[ALERT] Backup replica '{replica_name}' (port {replica_ports}) in project '{proj}' is DOWN!\n"
                            f"        The primary service '{svc_name}' is healthy on port {primary_ports}.{RESET}"
                        )
                    elif not primary_running and not replica_running:
                        alerts.append(
                            f"{RED}[WARNING] Both primary '{svc_name}' and replica '{replica_name}' are DOWN in project '{proj}'!{RESET}"
                        )
                else:
                    # Non-replicated service
                    running = c.state.status == "running"
                    if not running:
                        ports = get_container_ports(c)
                        alerts.append(
                            f"{RED}[ALERT] Service '{svc_name}' (port {ports}) in project '{proj}' is DOWN! (No replica copy exists){RESET}"
                        )
                        if c not in unreplicated_failed:
                            unreplicated_failed.append(c)
            except Exception:
                continue
    return alerts, unreplicated_failed

def check_running_projects(containers):
    """Lists all Docker Compose projects that have running services, along with status."""
    print_header("RUNNING PROJECT SYSTEMS")
    if not containers:
        print(f"{YELLOW}No containers found on the system.{RESET}")
        return

    # Group by project working dir or project name
    projects = {}
    for c in containers:
        try:
            labels = c.config.labels
            proj_dir = labels.get("com.docker.compose.project.working_dir")
            proj_name = labels.get("com.docker.compose.project")
            if proj_name:
                folder_name = os.path.basename(proj_dir) if proj_dir else proj_name
                projects.setdefault(proj_name, {
                    "folder": folder_name,
                    "path": proj_dir or "Unknown",
                    "containers": []
                })["containers"].append(c)
        except Exception:
            continue

    running_projects_count = 0
    for proj_name, info in projects.items():
        try:
            # Check if project has at least one running container
            has_running = any(c.state.status == "running" for c in info["containers"])
            if not has_running:
                continue

            running_projects_count += 1
            print(f"{BOLD}{BLUE}Project: {info['folder']}{RESET}")
            print(f"  Path: {info['path']}")
            print(f"  Services:")
            
            # Table of services in this project
            print(f"    {'SERVICE':<20} {'CONTAINER NAME':<30} {'STATUS':<15} {'PORTS':<25}")
            print("    " + "-" * 90)
            for c in info["containers"]:
                try:
                    svc = c.config.labels.get("com.docker.compose.service", "unknown")
                    status_color = GREEN if c.state.status == "running" else RED
                    status = f"{status_color}{c.state.status}{RESET}"
                    ports_str = get_container_ports(c)
                    print(f"    {svc:<20} {c.name[:29]:<30} {status:<15} {ports_str:<25}")
                except Exception:
                    continue
            print("    " + "-" * 90 + "\n")
        except Exception:
            continue

    if running_projects_count == 0:
        print(f"{YELLOW}No running Docker Compose projects found.{RESET}")

def create_container_wizard():
    """Interactive wizard to spin up a Docker Compose project and configure host ports + redundancy."""
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
        script_dir = os.path.dirname(os.path.abspath(__file__))
        workspace_root = os.path.dirname(script_dir)
        for idx, d in enumerate(dirs, 1):
            rel_path = os.path.relpath(d, workspace_root)
            print(f"  [{idx}] {rel_path}")
        print("  [0] Specify a custom directory path")
        
        choice = get_input("Select a directory", "1")
        try:
            choice_idx = int(choice)
            if choice_idx == 0:
                custom_path = get_input("Enter custom directory path")
                if not custom_path or not os.path.exists(custom_path):
                    print(f"{RED}Invalid path. Cancelled.{RESET}")
                    return
                selected_dir = os.path.abspath(custom_path)
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
    
    # Auto-assign free ports for all original services
    for s_name, ports, is_db in target_candidates:
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

                assigned = get_free_port(host_port, used_ports)
                new_service_ports.append(f"{assigned}:{container_port}")
                used_ports.add(assigned)
                print(f"  {CYAN}Service '{s_name}': assigned host port {assigned} → container port {container_port}{RESET}")
            else:
                new_service_ports.append(p)
        override_ports[s_name] = new_service_ports
        
    # Check if auto-replication is configured for this project
    auto_replicate = is_auto_replicate_enabled(selected_dir)
    if auto_replicate:
        print(f"\n{GREEN}⚡ Auto-Replication is ENABLED for this project. Backup replicas will be created automatically.{RESET}")

    # Check if redundancy replica is wanted
    if auto_replicate:
        replicate_choice = 'y'
        print(f"{CYAN}Auto-replicating all eligible non-database services...{RESET}")
    else:
        replicate_choice = get_input("\nWould you like to deploy redundant/backup replica copies of any services? (y/n)", "n")
    replica_configs = {}
    named_vols_to_declare = []

    if replicate_choice.lower() == 'y':
        # Exclude DB services from eligible replicas (as DBs should not be duplicated to share the database data)
        rep_candidates = [c[0] for c in target_candidates if not c[2]]
        if not rep_candidates:
            print(f"{YELLOW}No non-database services found that can be replicated.{RESET}")
        else:
            if auto_replicate:
                replicate_services = rep_candidates
                print(f"Auto-replicating services: {', '.join(replicate_services)}")
            else:
                print("\nEligible services for replication (excluding databases):")
                for idx, s_name in enumerate(rep_candidates, 1):
                    print(f"  [{idx}] {s_name}")
                print("  [all] Replicate all eligible services")

                sel = get_input("Choose services to replicate (comma-separated numbers, e.g. '1,2', 'all', or '0' to cancel)", "all")
                replicate_services = []
                if sel == "0":
                    print("Replication cancelled.")
                elif sel.lower() == "all":
                    replicate_services = rep_candidates
                else:
                    try:
                        indices = [int(x.strip()) for x in sel.split(",")]
                        for idx in indices:
                            if 1 <= idx <= len(rep_candidates):
                                replicate_services.append(rep_candidates[idx - 1])
                    except ValueError:
                        print(f"{RED}Invalid input. Skipping replica creation.{RESET}")

            for selected_service in replicate_services:
                print(f"\n{GREEN}Configuring backup replica for service '{selected_service}'...{RESET}")

                # Clone original config
                replica_config = services[selected_service].copy()

                # Update container_name to avoid conflict
                if "container_name" in replica_config:
                    replica_config["container_name"] = replica_config["container_name"] + "_replica_2"

                # Configure different ports for the replica
                orig_ports = replica_config.get("ports", [])
                new_replica_ports = []
                for p in orig_ports:
                    if isinstance(p, str) and ":" in p:
                        host_port_str, container_port_str = p.split(":")
                        try:
                            host_port = int(host_port_str)
                            container_port = int(container_port_str)
                        except ValueError:
                            new_replica_ports.append(p)
                            continue

                        suggested_replica = get_free_port(host_port + 1, used_ports)
                        # Always auto-assign the next free port and just display it
                        selected_port = suggested_replica
                        print(f"  {CYAN}Replica '{selected_service}_replica_2': assigned host port {selected_port} → container port {container_port}{RESET}")
                        new_replica_ports.append(f"{selected_port}:{container_port}")
                        used_ports.add(selected_port)
                    else:
                        new_replica_ports.append(p)
                replica_config["ports"] = new_replica_ports
                
                # Configure isolated volume paths for the replica to prevent disk conflicts
                orig_volumes = replica_config.get("volumes", [])
                if orig_volumes:
                    new_replica_vols, named_vols = duplicate_volumes(orig_volumes)
                    replica_config["volumes"] = new_replica_vols
                    named_vols_to_declare.extend(named_vols)
                
                replica_configs[f"{selected_service}_replica_2"] = replica_config

    # Build override dictionary
    override_data = {
        "version": data.get("version", "3.8"),
        "services": {}
    }
    
    # 1. Add port overrides for original services
    for s_name, ports_list in override_ports.items():
        override_data["services"][s_name] = {"ports": ports_list}
        
    # 2. Add replica copies if configured
    for r_name, r_config in replica_configs.items():
        override_data["services"][r_name] = r_config
        
    # 3. Add named volumes if any need declaring
    if named_vols_to_declare:
        override_data["volumes"] = {vol: {} for vol in named_vols_to_declare}
        
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
    print_header("START / STOP / RESTART CONTAINERS")
    selected = select_containers_prompt(containers, "control")
    if not selected:
        return
    
    print(f"\nSelected container(s):")
    for c in selected:
        try:
            print(f"  - {BOLD}{c.name}{RESET} ({c.id[:12]}) - Status: {c.state.status}")
        except Exception:
            print(f"  - (Unknown/Deleted container)")
            
    print("\nActions:")
    print("  [1] Start")
    print("  [2] Stop")
    print("  [3] Restart")
    print("  [0] Cancel")
    
    action = get_input("Select action for all selected containers", "1")
    if action not in ["1", "2", "3"]:
        print("Cancelled.")
        return
        
    for c in selected:
        try:
            # Re-fetch container state in case it changed
            try:
                c = docker.container.inspect(c.id)
            except Exception:
                print(f"{RED}Container '{getattr(c, 'name', 'unknown')}' could not be inspected. Skipping.{RESET}")
                continue
                
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
        except DockerException as e:
            print(f"{RED}Operation failed for '{getattr(c, 'name', 'unknown')}': {e}{RESET}")

def remove_container_action(containers):
    """Deletes selected containers."""
    print_header("REMOVE CONTAINERS")
    selected = select_containers_prompt(containers, "remove")
    if not selected:
        return
    
    print("\nYou have selected the following container(s) for removal:")
    for c in selected:
        try:
            print(f"  - {BOLD}{c.name}{RESET} ({c.id[:12]})")
        except Exception:
            pass
            
    confirm = get_input(f"\nAre you sure you want to permanently remove all {len(selected)} container(s)? (y/n)", "n").lower()
    if confirm != 'y':
        print("Cancelled.")
        return
        
    force = get_input("Force remove? (Stops containers if running) (y/n)", "n").lower()
    for c in selected:
        try:
            c_name = getattr(c, 'name', 'unknown')
            print(f"Removing container '{c_name}'...")
            docker.container.remove(c, force=(force == 'y'))
            print(f"{GREEN}Container '{c_name}' removed successfully.{RESET}")
        except DockerException as e:
            print(f"{RED}Failed to remove container: {e}{RESET}")

def rename_container_action(containers):
    """Renames selected containers."""
    print_header("RENAME CONTAINERS")
    selected = select_containers_prompt(containers, "rename")
    if not selected:
        return
        
    for c in selected:
        try:
            # Re-fetch container state to get latest name
            try:
                c = docker.container.inspect(c.id)
            except Exception:
                print(f"{RED}Container '{getattr(c, 'name', 'unknown')}' could not be inspected. Skipping.{RESET}")
                continue
                
            print(f"\nCurrent name: {BOLD}{c.name}{RESET} ({c.id[:12]})")
            new_name = get_input(f"Enter new name for '{c.name}' (or press Enter to skip)")
            if not new_name:
                print(f"{YELLOW}Skipped renaming for '{c.name}'.{RESET}")
                continue
                
            print(f"Renaming container '{c.name}' to '{new_name}'...")
            docker.container.rename(c, new_name)
            print(f"{GREEN}Container renamed successfully.{RESET}")
        except DockerException as e:
            print(f"{RED}Failed to rename container: {e}{RESET}")

def logs_action(containers):
    """Displays or streams logs for selected containers."""
    print_header("VIEW CONTAINER LOGS")
    selected = select_containers_prompt(containers, "view logs")
    if not selected:
        return
        
    print("\nLog Options:")
    print("  [1] View last 100 lines")
    print("  [2] Stream logs (Ctrl+C to stop/next)")
    choice = get_input("Select option for all selected containers", "1")
    
    if choice not in ["1", "2"]:
        print("Invalid option.")
        return
        
    for c in selected:
        try:
            # Re-fetch container state
            try:
                c = docker.container.inspect(c.id)
            except Exception:
                print(f"{RED}Container '{getattr(c, 'name', 'unknown')}' could not be inspected. Skipping.{RESET}")
                continue
                
            if choice == "1":
                logs = docker.container.logs(c, tail=100)
                print(f"\n{BOLD}=== Logs for {c.name} (Last 100 lines) ==={RESET}\n")
                print(logs.decode('utf-8', errors='replace') if isinstance(logs, bytes) else logs)
                print(f"\n{BOLD}=== End of Logs for {c.name} ==={RESET}\n")
            elif choice == "2":
                print(f"\n{BOLD}=== Streaming logs for {c.name} (Press Ctrl+C to stop/next) ==={RESET}\n")
                try:
                    for stream_type, content in docker.container.logs(c, stream=True):
                        sys.stdout.write(content.decode('utf-8', errors='replace'))
                        sys.stdout.flush()
                except KeyboardInterrupt:
                    print(f"\n{YELLOW}Stopped streaming logs for {c.name}.{RESET}")
                    if len(selected) > 1 and c != selected[-1]:
                        cont = get_input("Proceed to next container's logs? (y/n)", "y").lower()
                        if cont != 'y':
                            break
        except DockerException as e:
            print(f"{RED}Failed to read logs for {getattr(c, 'name', 'unknown')}: {e}{RESET}")

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
    """Changes the port mapping of selected containers by stopping, removing, and recreating them."""
    print_header("CHANGE CONTAINER PORT MAPPING")
    selected = select_containers_prompt(containers, "change port mapping")
    if not selected:
        return
        
    for c in selected:
        try:
            print(f"\nConfiguring port mapping for container: {BOLD}{c.name}{RESET} ({c.id[:12]})")
            
            try:
                c = docker.container.inspect(c.id)
            except Exception:
                print(f"{RED}Container '{getattr(c, 'name', 'unknown')}' could not be inspected. Skipping.{RESET}")
                continue
                
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
                    print(f"{RED}Invalid container port. Skipping container.{RESET}")
                    continue
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
                            print(f"{RED}Invalid choice. Skipping container.{RESET}")
                            continue
                    except ValueError:
                        print(f"{RED}Invalid choice. Skipping container.{RESET}")
                        continue
                else:
                    old_host_port, container_port = mapped_ports[0]
                    
            print(f"Current mapping: Host {old_host_port if old_host_port else 'None'} -> Container {container_port}")
            
            used_ports = get_used_host_ports()
            suggested = get_free_port(old_host_port if old_host_port else 8080, used_ports)
            
            ans = get_input(f"Enter new host port for container port {container_port}", str(suggested))
            try:
                new_host_port = int(ans)
            except ValueError:
                print(f"{RED}Invalid port. Skipping container.{RESET}")
                continue
                
            if new_host_port in used_ports and new_host_port != old_host_port:
                print(f"{RED}Port {new_host_port} is already in use by another container!{RESET}")
                confirm = get_input("Do you want to proceed anyway? (y/n)", "n").lower()
                if confirm != 'y':
                    continue
                    
            envs = {}
            if c.config.env:
                for item in c.config.env:
                    if '=' in item:
                        k, v = item.split('=', 1)
                        envs[k] = v
                        
            volumes = []
            if c.mounts:
                for m in c.mounts:
                    if m.type == 'volume':
                        volumes.append((m.name, m.destination))
                    elif m.type == 'bind':
                        volumes.append((m.source, m.destination))
                        
            networks = list(c.network_settings.networks.keys()) if c.network_settings.networks else []
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
                print("Skipped.")
                continue
                
            print(f"Stopping container '{name}'...")
            c.stop()
            print(f"Removing container '{name}'...")
            docker.container.remove(c)
            
            print(f"Launching container '{name}' with new port mapping...")
            run_kwargs = {
                "detach": True,
                "publish": publish,
                "restart": restart_policy
            }
            if envs:
                run_kwargs["envs"] = envs
            if volumes:
                run_kwargs["volumes"] = volumes
            if first_network:
                run_kwargs["networks"] = [first_network]
                
            new_c = docker.run(
                image,
                name=name,
                **run_kwargs
            )
            
            for net in additional_networks:
                try:
                    docker.network.connect(net, new_c)
                except Exception as ne:
                    print(f"{YELLOW}Warning: Could not reconnect to network '{net}': {ne}{RESET}")
                    
            print(f"{GREEN}Success! Container '{name}' port changed. Host {new_host_port} -> Container {container_port}.{RESET}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"{RED}Failed to change port mapping: {e}{RESET}")

def cleanup_exited_containers():
    """Cleans up all exited/stopped containers from the system."""
    print_header("CLEAN UP EXITED CONTAINERS")
    try:
        containers = docker.container.list(all=True)
        exited = [c for c in containers if c.state.status not in ["running", "paused"]]
        if not exited:
            print(f"{GREEN}No exited containers to clean up.{RESET}")
            return
            
        print(f"Found {len(exited)} exited containers:")
        for idx, c in enumerate(exited, 1):
            print(f"  [{idx}] {c.name} ({c.config.image})")
            
        confirm = get_input("\nAre you sure you want to permanently remove all of these containers? (y/n)", "n")
        if confirm.lower() == 'y':
            print("Removing containers...")
            removed_count = 0
            for c in exited:
                c_name = getattr(c, 'name', 'unknown')
                try:
                    docker.container.remove(c)
                    removed_count += 1
                except Exception as e:
                    print(f"{RED}Failed to remove container '{c_name}': {e}{RESET}")
            print(f"{GREEN}Successfully removed {removed_count} containers.{RESET}")
    except Exception as e:
        print(f"{RED}Failed to clean up containers: {e}{RESET}")

def manage_projects_action():
    """Manage Compose Projects (Start, Stop, Restart, Rebuild, View Logs)."""
    print_header("MANAGE COMPOSE PROJECTS")
    
    # Scan/Find compose directories in workspace
    dirs = scan_compose_dirs()
    if not dirs:
        print(f"{YELLOW}No Docker Compose projects found in the workspace.{RESET}")
        return
        
    print("Select a Docker Compose Project:")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_root = os.path.dirname(script_dir)
    for idx, d in enumerate(dirs, 1):
        rel_path = os.path.relpath(d, workspace_root)
        print(f"  [{idx}] {rel_path}")
    print("  [0] Cancel")
    
    choice = get_input("Choose project", "1")
    try:
        choice_idx = int(choice)
        if choice_idx == 0:
            return
        if 1 <= choice_idx <= len(dirs):
            selected_dir = dirs[choice_idx - 1]
        else:
            print(f"{RED}Invalid choice.{RESET}")
            return
    except ValueError:
        print(f"{RED}Invalid choice.{RESET}")
        return
        
    compose_path = os.path.join(selected_dir, "docker-compose.yml")
    override_path = os.path.join(selected_dir, "docker-compose.override.yml")
    
    # Check what override files exist
    compose_files = [os.path.abspath(compose_path)]
    if os.path.exists(override_path):
        compose_files.append(os.path.abspath(override_path))
        
    project_name = os.path.basename(selected_dir)
    print(f"\nSelected Project: {BOLD}{project_name}{RESET} ({os.path.relpath(selected_dir, workspace_root)})")
    
    print("Actions:")
    print("  [1] Start / Up Project (docker compose up -d)")
    print("  [2] Stop / Down Project (docker compose down)")
    print("  [3] Restart Project (docker compose restart)")
    print("  [4] Rebuild & Up Project (docker compose up -d --build)")
    print("  [5] View Merged Project Logs (Ctrl+C to stop)")
    print("  [0] Cancel")
    
    action = get_input("Choose action", "1")
    if action == "0":
        return
        
    from python_on_whales import DockerClient
    client = DockerClient(compose_files=compose_files)
    
    try:
        if action == "1":
            print(f"Starting compose services for project '{project_name}'...")
            client.compose.up(detach=True)
            print(f"{GREEN}Project started successfully.{RESET}")
        elif action == "2":
            print(f"Stopping/Removing compose services for project '{project_name}'...")
            client.compose.down()
            print(f"{GREEN}Project stopped/downed successfully.{RESET}")
        elif action == "3":
            print(f"Restarting compose services for project '{project_name}'...")
            client.compose.restart()
            print(f"{GREEN}Project restarted successfully.{RESET}")
        elif action == "4":
            print(f"Rebuilding and starting compose services for project '{project_name}'...")
            client.compose.up(detach=True, build=True)
            print(f"{GREEN}Project rebuilt and started successfully.{RESET}")
        elif action == "5":
            print(f"\n{BOLD}=== Logs for Project {project_name} (Press Ctrl+C to stop) ==={RESET}\n")
            try:
                for stream_type, content in client.compose.logs(stream=True):
                    sys.stdout.write(content.decode('utf-8', errors='replace'))
                    sys.stdout.flush()
            except KeyboardInterrupt:
                print(f"\n{YELLOW}Stopped streaming project logs.{RESET}")
        else:
            print("Invalid action.")
    except Exception as e:
        print(f"{RED}Operation failed: {e}{RESET}")

def parse_replication_targets(input_str, containers):
    """Parses user input tokens (container names, service names, or project folder names)
    and returns a set of tuples: (project_dir, service_name)."""
    tokens = [t.strip() for t in input_str.replace(',', ' ').split() if t.strip()]
    targets = set()
    
    dirs = scan_compose_dirs()
    folder_to_dir = {os.path.basename(d): d for d in dirs}
    
    for token in tokens:
        # Case 1: Folder name matches compose project
        if token in folder_to_dir:
            proj_dir = folder_to_dir[token]
            compose_path = os.path.join(proj_dir, "docker-compose.yml")
            if os.path.exists(compose_path):
                try:
                    with open(compose_path, 'r') as f:
                        data = yaml.safe_load(f)
                        services = data.get("services", {})
                        for s_name in services.keys():
                            if not s_name.endswith("_replica_2"):
                                targets.add((proj_dir, s_name))
                except Exception:
                    pass
            continue
            
        # Case 2: Match container name or service name
        for c in containers:
            try:
                if c.name == token or token in c.name:
                    labels = c.config.labels
                    proj_dir = labels.get("com.docker.compose.project.working_dir")
                    svc_name = labels.get("com.docker.compose.service")
                    if proj_dir and svc_name and not svc_name.endswith("_replica_2"):
                        targets.add((os.path.abspath(proj_dir), svc_name))
                        
                labels = c.config.labels
                svc_name = labels.get("com.docker.compose.service")
                proj_dir = labels.get("com.docker.compose.project.working_dir")
                if svc_name == token and proj_dir:
                    if not svc_name.endswith("_replica_2"):
                        targets.add((os.path.abspath(proj_dir), svc_name))
            except Exception:
                continue
    return targets

def merge_replica_to_override(project_dir, service_name, replica_config, named_volumes):
    """Merges a new replica service config into the project's docker-compose.override.yml."""
    override_path = os.path.join(project_dir, "docker-compose.override.yml")
    
    override_data = {"version": "3.8", "services": {}}
    if os.path.exists(override_path):
        try:
            with open(override_path, 'r') as f:
                loaded = yaml.safe_load(f)
                if isinstance(loaded, dict):
                    override_data = loaded
        except Exception as e:
            print(f"{YELLOW}Warning: Could not read existing override file: {e}. Recreating.{RESET}")
            
    if "services" not in override_data or not isinstance(override_data["services"], dict):
        override_data["services"] = {}
        
    replica_service_name = f"{service_name}_replica_2"
    override_data["services"][replica_service_name] = replica_config
    
    if named_volumes:
        if "volumes" not in override_data or not isinstance(override_data["volumes"], dict):
            override_data["volumes"] = {}
        for vol in named_volumes:
            override_data["volumes"][vol] = {}
            
    try:
        if os.path.exists(override_path):
            os.replace(override_path, override_path + ".bak")
        with open(override_path, 'w') as f:
            yaml.dump(override_data, f)
        print(f"{GREEN}Updated override file: {override_path}{RESET}")
        return True
    except Exception as e:
        print(f"{RED}Failed to write override file: {e}{RESET}")
        return False

def deploy_replicas_for_failed(unreplicated_failed, containers):
    """Deploys replica copies for chosen failed services."""
    print_header("DEPLOY BACKUP REPLICAS FOR FAILED SERVICES")
    print("Failed unreplicated services found:")
    for idx, c in enumerate(unreplicated_failed, 1):
        try:
            svc = c.config.labels.get("com.docker.compose.service", "unknown")
            proj = c.config.labels.get("com.docker.compose.project", "unknown")
            print(f"  [{idx}] Service '{svc}' in project '{proj}' (Container: '{c.name}')")
        except Exception:
            continue
            
    user_input = get_input("\nEnter container name(s), service name(s), or project folder name(s) to replicate (separated by spaces/commas)")
    if not user_input:
        print("Cancelled.")
        return
        
    targets = parse_replication_targets(user_input, containers)
    if not targets:
        print(f"{RED}No matching compose services found for replication.{RESET}")
        return
        
    used_ports = get_used_host_ports()
    
    for project_dir, service_name in targets:
        print(f"\n{GREEN}Configuring backup replica for service '{service_name}' in project folder '{os.path.basename(project_dir)}'...{RESET}")
        
        compose_path = os.path.join(project_dir, "docker-compose.yml")
        if not os.path.exists(compose_path):
            print(f"{RED}Error: docker-compose.yml not found in '{project_dir}'{RESET}")
            continue
            
        try:
            with open(compose_path, 'r') as f:
                data = yaml.safe_load(f)
        except Exception as e:
            print(f"{RED}Failed to parse docker-compose.yml in '{project_dir}': {e}{RESET}")
            continue
            
        services = data.get("services", {})
        if service_name not in services:
            print(f"{RED}Service '{service_name}' not found in docker-compose.yml.{RESET}")
            continue
            
        replica_config = services[service_name].copy()
        
        if "container_name" in replica_config:
            replica_config["container_name"] = replica_config["container_name"] + "_replica_2"
            
        orig_ports = replica_config.get("ports", [])
        new_replica_ports = []
        for p in orig_ports:
            if isinstance(p, str) and ":" in p:
                host_port_str, container_port_str = p.split(":")
                try:
                    host_port = int(host_port_str)
                    container_port = int(container_port_str)
                except ValueError:
                    new_replica_ports.append(p)
                    continue
                
                suggested_replica = get_free_port(host_port + 1, used_ports)
                ans = get_input(f"Enter host port for container port {container_port} (replica copy: {service_name}_replica_2)", str(suggested_replica))
                try:
                    selected_port = int(ans)
                    new_replica_ports.append(f"{selected_port}:{container_port}")
                    used_ports.add(selected_port)
                except ValueError:
                    print(f"{YELLOW}Invalid port input. Keeping default.{RESET}")
                    new_replica_ports.append(p)
            else:
                new_replica_ports.append(p)
        replica_config["ports"] = new_replica_ports
        
        orig_volumes = replica_config.get("volumes", [])
        named_vols_to_declare = []
        if orig_volumes:
            new_replica_vols, named_vols = duplicate_volumes(orig_volumes)
            replica_config["volumes"] = new_replica_vols
            named_vols_to_declare.extend(named_vols)
            
        success = merge_replica_to_override(project_dir, service_name, replica_config, named_vols_to_declare)
        if success:
            try:
                print(f"{CYAN}Running docker compose up for project...{RESET}")
                from python_on_whales import DockerClient
                compose_files = [os.path.abspath(compose_path)]
                override_path = os.path.join(project_dir, "docker-compose.override.yml")
                if os.path.exists(override_path):
                    compose_files.append(os.path.abspath(override_path))
                    
                client = DockerClient(compose_files=compose_files)
                client.compose.up(detach=True)
                print(f"{GREEN}Successfully deployed replica for '{service_name}'!{RESET}")
            except Exception as ce:
                print(f"{RED}Failed to deploy replica: {ce}{RESET}")

def configure_auto_replication_action():
    """Lets the user toggle automatic backup replica creation ON/OFF per project folder.
    When enabled, deploying a project via Option 2 will automatically create a replica
    copy of every eligible (non-database) service without prompting."""
    print_header("CONFIGURE AUTO-REPLICATION PER PROJECT")

    dirs = scan_compose_dirs()
    if not dirs:
        print(f"{YELLOW}No Docker Compose project folders found in the workspace.{RESET}")
        return

    cfg = load_manager_config()
    auto_list = [os.path.abspath(p) for p in cfg.get("auto_replicate_projects", [])]

    script_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_root = os.path.dirname(script_dir)

    print("\nProject folders found in workspace (⚡ = auto-replication ON):\n")
    for idx, d in enumerate(dirs, 1):
        rel = os.path.relpath(d, workspace_root)
        marker = f"{GREEN}⚡ ON {RESET}" if os.path.abspath(d) in auto_list else f"{RED}OFF{RESET}"
        print(f"  [{idx}] [{marker}] {rel}")
    print("  [all] Toggle ALL projects")
    print("  [0] Back")

    choice = get_input("\nEnter project number(s) to toggle (space/comma-separated)", "0")
    if not choice or choice.strip() == "0":
        return

    normalized = choice.replace(',', ' ').strip()
    parts = normalized.split()

    targets = []
    if len(parts) == 1 and parts[0].lower() == "all":
        targets = list(range(len(dirs)))
    else:
        for part in parts:
            try:
                idx = int(part)
                if 1 <= idx <= len(dirs):
                    targets.append(idx - 1)
                else:
                    print(f"{RED}Invalid index {part} — skipping.{RESET}")
            except ValueError:
                print(f"{RED}Invalid input '{part}' — skipping.{RESET}")

    if not targets:
        print("No valid selections. Cancelled.")
        return

    changed = []
    for t_idx in targets:
        d = os.path.abspath(dirs[t_idx])
        rel = os.path.relpath(d, workspace_root)
        if d in auto_list:
            auto_list.remove(d)
            print(f"  {RED}⚡ Auto-replication DISABLED{RESET} for '{rel}'")
        else:
            auto_list.append(d)
            print(f"  {GREEN}⚡ Auto-replication ENABLED{RESET} for '{rel}'")
        changed.append(rel)

    cfg["auto_replicate_projects"] = auto_list
    if save_manager_config(cfg):
        print(f"\n{GREEN}Configuration saved. Changes apply to future deployments (Option 2).{RESET}")
    else:
        print(f"\n{RED}Failed to save configuration.{RESET}")


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
        print(f"                        DOCKER INTERACTIVE MANAGER v2                     ")
        print(f"=========================================================================={RESET}")
        
        containers = []
        try:
            containers = list_containers()
            # Active failover & health alerts for replicas
            alerts, unreplicated_failed = check_failover_alerts(containers)
            if alerts:
                print(f"\n{BOLD}{YELLOW}=== FAILOVER & HEALTH ALERTS ==={RESET}")
                for alert in alerts:
                    print(alert)
                print(f"{BOLD}{YELLOW}================================{RESET}")
                
                if unreplicated_failed:
                    ans = get_input("\nWould you like to deploy backup replica copies for any of these failed services? (y/n)", "n")
                    if ans.lower() == 'y':
                        deploy_replicas_for_failed(unreplicated_failed, containers)
                        # Refresh containers list after replication
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
        print("  [11] Check running project systems")
        print("  [12] Manage Compose Projects (Start/Stop/Logs/Rebuild)")
        print("  [13] Clean up exited containers")
        print("  [14] Configure Auto-Replication for Project Folders")
        print("  [15] Exit")

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
            check_running_projects(containers)
        elif choice == "12":
            manage_projects_action()
        elif choice == "13":
            cleanup_exited_containers()
        elif choice == "14":
            configure_auto_replication_action()
        elif choice == "15":
            print(f"\n{GREEN}Goodbye!{RESET}")
            break
        else:
            print(f"{RED}Invalid option!{RESET}")

        input(f"\nPress Enter to return to main menu...")

if __name__ == "__main__":
    main()
