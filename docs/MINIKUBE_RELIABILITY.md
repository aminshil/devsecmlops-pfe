# Minikube reliability: why it broke after a reboot, and the fix

## Symptom
After a reboot, VM stop or pause, `kubectl` reported the cluster unreachable. A plain
`minikube start` did not bring it back, and `scripts/k8s_recover.sh` fell through to a full
rebuild, which also deleted the database.

## Root cause (three faults stacked)
1. **Boot-order race over a fixed address.** Minikube's Docker network always gives the
   cluster node `192.168.49.2`. Docker restarts the Jenkins container (restart policy) before
   anything starts Minikube, and Jenkins, being attached to the same network, could take
   `.2`. The cluster container then failed with "address already in use".
2. **Nothing started the cluster.** The `minikube` container has restart policy `no`, so
   after a reboot it stayed down until someone ran `minikube start` by hand.
3. **Missing driver flag as root.** `minikube start --driver=docker` as root is refused
   unless `--force` is given, so the gentle start failed and recovery escalated to a rebuild.

## Fix
| Piece | Where | Effect |
|---|---|---|
| Jenkins pinned to `192.168.49.10` | `docker network connect --ip`, re-applied by `scripts/k8s_recover.sh` | `.2` can no longer be taken by Jenkins |
| Boot-time unit | `scripts/systemd/minikube-start.service` (install in `/etc/systemd/system/`, `systemctl enable`) | starts the cluster after Docker, with `--force` |
| Hardened recovery | `scripts/k8s_recover.sh` | never evicts the `minikube` container, reconnects Jenkins on `.10`, longer gentle start, saves and restores judged feedback around a rebuild |

## Verified
After a VM reboot the cluster came back by itself, with no recovery script: the node, the
PostgreSQL pod and its persistent volume were the same objects as before the reboot, and
the database kept all its data (60,699 predictions, 180 operator verdicts).

## Alternative tested and rejected: Docker `restart=always`
Setting `docker update --restart=always minikube` and disabling the systemd unit was tried.
After a reboot the container came back (`host: Running`) but `kubelet` and `apiserver` stayed
`Stopped`, so `kubectl` was refused and the API returned no response. Only `minikube start`
starts Kubernetes inside the container, so the systemd unit is required. Jenkins staying
pinned at `192.168.49.10` held in that test. Restored with `minikube start --driver=docker
--force`, no data lost. Two further reboots with the unit alone came back by themselves.

## Check after any reboot
    systemctl is-active minikube-start.service
    kubectl get nodes && kubectl -n ml-serving get pods
    docker inspect jenkins --format '{{(index .NetworkSettings.Networks "minikube").IPAddress}}'   # 192.168.49.10

## Known limits
- The VM pause/resume scenario is not covered by a test (a clock jump after resume is the
  suspected remaining cause).
- A full rebuild (only run by `k8s_recover.sh` if the gentle start fails) recreates the
  database volume; judged feedback is restored from `feedback_backup.tsv` if one exists.
- Minikube is a single-node development cluster; k3s is the planned production alternative.
