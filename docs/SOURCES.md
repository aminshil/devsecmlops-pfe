# Sources

Every external source this project took information from, grouped by what it informed. Nothing else in
the repository comes from outside: every dataset is simulated by `ml-model/generate_telecom_fleet.py`
(except the public SMD benchmark below), and every number in the README is the output of a script in
this repository, run on that data.

Each entry says what was taken from it. The README cites the same sources where they are used; section
9c keeps its own numbered list for the failure model.

## 1. Network failures, gray failure and root cause (failure model v3, Fleet tab, README 9c)

1. P. Gill, N. Jain, N. Nagappan, *Understanding Network Failures in Data Centers: Measurement, Analysis,
   and Implications*, ACM SIGCOMM 2011. Failure rates per device type, durations, traffic during failures,
   redundancy effectiveness. https://people.cs.umass.edu/~phillipa/papers/comm235-gill.pdf
2. P. Huang et al., *Gray Failure: The Achilles' Heel of Cloud-Scale Systems*, HotOS 2017. Differential
   observability, silent packet drops.
   https://www.microsoft.com/en-us/research/publication/gray-failure-achilles-heel-cloud-scale-systems/
   (summary used: https://blog.acolyer.org/2017/06/15/gray-failure-the-achilles-heel-of-cloud-scale-systems/)
3. B. Arzani et al., *007: Democratically Finding the Cause of Packet Drops*, USENIX NSDI 2018. Silent drops
   invisible to SNMP; host TCP retransmissions as the signal. https://arxiv.org/pdf/1802.07222
4. M. Mathis, J. Semke, J. Mahdavi, T. Ott, *The Macroscopic Behavior of the TCP Congestion Avoidance
   Algorithm*, ACM SIGCOMM CCR 1997. TCP throughput proportional to 1/√p.
   https://courses.cs.duke.edu/fall24/compsci514/readings/mathis-tcpmodel-ccr97.pdf
5. J. Meza et al., *A Large Scale Study of Data Center Network Reliability*, ACM IMC 2018. Rack-switch
   incidents, single switch per rack, root-cause categories.
   https://conferences.sigcomm.org/imc/2018/papers/imc18-final179.pdf
6. Nagios, *Parent/child host relationships and network reachability*. UNREACHABLE vs DOWN, notification
   suppression. https://support.nagios.com/kb/print-904.html
7. Grafana Labs, *Handle connectivity errors in alerts*. `up == 0`, missing data.
   https://grafana.com/docs/grafana/v12.4/alerting/guides/connectivity-errors/
8. B. Beyer, C. Jones, J. Petoff, N. R. Murphy (eds.), *Site Reliability Engineering*, O'Reilly 2016,
   ch. 22 "Addressing Cascading Failures". https://sre.google/sre-book/addressing-cascading-failures/
9. B. Gregg, *Linux Load Averages: Solving the Mystery*, 2017. What the load average counts.
   https://www.brendangregg.com/blog/2017-08-08/linux-load-averages.html
10. L. Pham et al., *RCAEval: A Benchmark for Root Cause Analysis of Microservice Systems with Telemetry
    Data*, 2024. Fault injection and root-cause labelling. https://arxiv.org/html/2412.17015v1
11. OneUptime, *Which Network Metrics Catch Host Problems? Drops, Errors, and Saturation*, 2026.
    `node_netstat_Tcp_RetransSegs` and the retransmission ratio.
    https://oneuptime.com/blog/post/2026-07-31-host-network-metrics-drops-errors-retransmits-saturation/markdown
12. Facebook Engineering, *More details about the October 4 outage*, 2021. Maintenance command, backbone
    down, DNS withdrawal (the pattern listed under future work).
    https://engineering.fb.com/2021/10/05/networking-traffic/outage-details/

## 2. Real-data validation (README L0, SMD)

13. Y. Su, Y. Zhao, C. Niu, R. Liu, W. Sun, D. Pei, *Robust Anomaly Detection for Multivariate Time Series
    through Stochastic Recurrent Neural Network* (OmniAnomaly), ACM KDD 2019. The Server Machine Dataset
    (28 servers) used by `ml-model/load_smd.py`, the point-adjusted F1 convention, and the published
    results the README compares against. https://doi.org/10.1145/3292500.3330672 ; dataset and code:
    https://github.com/NetManAIOps/OmniAnomaly

## 3. Alerting, monitoring and the control panel's History tab (README L5)

14. Prometheus, *Alerting rules*: `for`, labels and annotations, and the `ALERTS` series used by the
    Grafana panel "Alerts firing now". https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/
15. Prometheus, *Recording rules*: `fleet:machine_anomalous:fresh`, `fleet:anomalous_machines:count`, and the
    rules that measure each machine's reporting interval and silence (`fleet:machine_silent` and the others).
    https://prometheus.io/docs/prometheus/latest/configuration/recording_rules/
16. Prometheus, *Unit testing for rules*: `promtool test rules`, `alert_rule_test` and `promql_expr_test`,
    the format of `monitoring/alerting/alert_rules_test.yml`.
    https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/
17. Prometheus, *Operators*: binary operator precedence (`==` binds tighter than `and`), the cause of the
    History-tab bug found against a real Prometheus. https://prometheus.io/docs/prometheus/latest/querying/operators/
18. Prometheus, *Functions*: `increase()` (extrapolation, counter resets; why the root-cause spotlight shows
    at most 14), `max_over_time()`, `label_replace()`; `quantile_over_time()`, `count_over_time()`,
    `last_over_time()`, `clamp_min()` and `absent()` (a machine's reporting interval, its silence, and
    `FleetNoData`). https://prometheus.io/docs/prometheus/latest/querying/functions/
19. Prometheus, *HTTP API*: range queries and the limit of 11,000 points per series (the History tab's
    step). https://prometheus.io/docs/prometheus/latest/querying/api/
20. Prometheus, *Querying basics*: staleness and the 5-minute lookback; the `offset` modifier (a machine not
    known a minute ago, in `MachineReportingAgain`).
    https://prometheus.io/docs/prometheus/latest/querying/basics/
21. Prometheus, *Alertmanager configuration* (0.28): routes, `group_wait`, `group_interval`,
    `repeat_interval`, `inhibit_rules`, `telegram_configs` with `bot_token_file`.
    https://prometheus.io/docs/alerting/0.28/configuration/
22. Prometheus, *Alertmanager notification template reference* (0.28): the `since` and `humanizeDuration`
    functions behind the running anomaly list (why Alertmanager is pinned to 0.28).
    https://prometheus.io/docs/alerting/0.28/notifications/
23. Prometheus, *Alertmanager API v2* (OpenAPI): `/api/v2/silences`, `/api/v2/silence/{id}`,
    `/api/v2/alerts`, used by the Notifications card (mute/unmute).
    https://github.com/prometheus/alertmanager/blob/main/api/v2/openapi.yaml
24. Prometheus, *Blackbox exporter*: the `http_2xx` module and `probe_success` behind `LayerDown` and the
    "Platform layers" panel. https://github.com/prometheus/blackbox_exporter
25. Telegram, *Bot API*: `getMe` (token check), `getUpdates` (chat id), `sendMessage` (4,096-character
    limit, HTML parse mode); bots are created with @BotFather. https://core.telegram.org/bots/api
26. Grafana Labs, *Provision Grafana*: the file provider that loads `monitoring/grafana/dashboards/` from
    the repository. https://grafana.com/docs/grafana/latest/administration/provisioning/
27. Grafana Labs, *Configure value mappings*: the UP/DOWN tiles and the coloured status text.
    https://grafana.com/docs/grafana/latest/panels-visualizations/configure-value-mappings/

## 4. Code quality and security gates (README L3, Security posture)

28. SonarSource, rule `javascript:S3358` *Ternary operators should not be nested* (stopped builds 113 and 114).
    https://rules.sonarsource.com/javascript/RSPEC-3358/ ; background:
    https://www.sonarsource.com/blog/stop-nesting-ternaries-javascript/
29. SonarSource, rule `python:S1192` *String literals should not be duplicated* (stopped build 108).
    https://rules.sonarsource.com/python/RSPEC-1192/
30. SonarSource, `eslint-plugin-sonarjs`: Sonar's JavaScript rules run locally to check the page code
    before a build. https://www.npmjs.com/package/eslint-plugin-sonarjs
31. Aqua Security, *Trivy*: repository scan (stage 3) and image scan with SBOM (stage 6).
    https://trivy.dev/
32. CVE-2026-53995 (PYSEC-2026-425), MLflow path traversal through the trace API in the FileStore backend,
    fixed in MLflow 3.17.2 (stopped build 115 at stage 3). https://osv.dev/vulnerability/CVE-2026-53995 ;
    https://osv.dev/vulnerability/PYSEC-2026-425 ; https://www.mend.io/vulnerability-database/CVE-2026-53995/ ;
    https://www.vulncheck.com/advisories/mlflow-path-traversal-via-trace-id-api
33. PyPI, *mlflow 3.17.2* (release of 21 October 2026, its dependency ranges checked against the pinned
    versions). https://pypi.org/project/mlflow/3.17.2/
34. MLflow, *Database migration* (`mlflow db upgrade`), run before restarting the tracking server on 3.17.2.
    https://mlflow.org/docs/latest/self-hosting/migration/
35. The pip-audit findings behind the security floors in `requirements.txt`: starlette
    PYSEC-2026-161, -248, -249, -2280, -2281 and pyasn1 PYSEC-2026-3455, -3456, -3457.
    https://osv.dev/vulnerability/PYSEC-2026-161 (each ID resolves at `https://osv.dev/vulnerability/<ID>`)

## 5. Assets

36. Lucide icons (ISC licence; portions from Feather, MIT): the equipment pictograms in
    `scripts/fleet_status.py` and the Fleet tab. https://lucide.dev

## 6. PFE report, Chapter 1 (host company, concepts, tool choices)

The bibliography of the report's first chapter (April 2026). The Tunisie Telecom and sector figures in
the report come from 37–40, 56 and 57.

37. Instance Nationale des Télécommunications (INT), *Rapport Financier du Secteur des Télécommunications
    2025*, 2026. Market shares, sector revenue, 5G launch, sector investment.
    https://www.intt.tn/fr/index-rapports-annuels-266-402.html
38. Challenges.tn, *Tunisie Telecom accélère l'IA souveraine : Data Centers certifiés et câble Medusa à
    22 Tbps*, November 2025. Data-centre certifications (ISO 27001, 27701, 9001).
    https://www.challenges.tn/high-tech/tunisie-telecom-accelere-l-ia-souveraine-data-centers-certifies-et-cable-medusa-a-22-tbps/
39. Tunisie Telecom, *Chiffre d'affaires et performances réseau* (official site), 2025. Network and
    submarine cables, Green Tech sites. https://www.tunisietelecom.tn/particulier/actualite/chiffre-d-affaires-tt/
40. nPerf, *Baromètre des connexions Internet mobiles en Tunisie, 01/01/2025 – 31/12/2025*, January 2026.
    nPoints, downlink and uplink speeds.
    https://media.nperf.com/files/publications/TN/TN-Barometer-mobile-connections-nPerf-2025_7894.pdf
41. N. Forsgren, J. Humble, G. Kim, *Accelerate: The Science of Lean Software and DevOps*, IT Revolution
    Press, 2018. DevOps and CI/CD. https://itrevolution.com/accelerate-book/
42. D. Sculley et al., *Hidden Technical Debt in Machine Learning Systems*, NIPS 2015. MLOps.
    https://papers.nips.cc/paper/2015/hash/86df7dcfd896fcaf2674f757a2463eba-Abstract.html
43. OWASP Foundation, *DevSecOps Guideline*, 2024. Shift-left security.
    https://owasp.org/www-project-devsecops-guideline/
44. S. Shankar et al., *Operationalizing Machine Learning: An Interview Study*, arXiv 2022. DevSecMLOps
    context. https://arxiv.org/abs/2209.09125
45. Jenkins, *User Documentation*. https://www.jenkins.io/doc/
46. M. Zaharia et al., *Accelerating the Machine Learning Lifecycle with MLflow*, IEEE Data Engineering
    Bulletin, 2018; MLflow documentation. https://mlflow.org/docs/latest/index.html
47. Docker, *Documentation*. https://docs.docker.com/
48. CNCF, *Kubernetes Documentation*. https://kubernetes.io/docs/home/
49. SonarSource, *SonarQube Documentation*. https://docs.sonarqube.org/
50. Aqua Security, *Trivy documentation* (report version of the link). https://aquasecurity.github.io/trivy/
51. CNCF, *Prometheus overview*. https://prometheus.io/docs/introduction/overview/
52. Grafana Labs, *Grafana documentation*. https://grafana.com/docs/
53. MinIO, *Documentation*. https://min.io/docs/minio/linux/index.html
54. Red Hat, *Ansible Documentation*. https://docs.ansible.com/
55. F. Pedregosa et al., *Scikit-learn: Machine Learning in Python*, JMLR 12, 2011.
    https://scikit-learn.org/stable/
56. L'Expert Journal, *4101 millions de dinars de chiffre d'affaires dans le secteur des
    télécommunications*, March 2026. https://lexpertjournal.net/fr/?p=139792
57. THD (Tunisie Haut Débit), *Rapport du régulateur tunisien : la data tire la croissance au troisième
    trimestre 2025*, November 2025.
    https://www.thd.tn/rapport-du-regulateur-tunisien-la-data-tire-la-croissance-au-troisieme-trimestre-2025/

## 7. Reference documentation for each layer (L0–L6)

The official page for each method or feature the layers rely on. The project's own tests, CI logs and
measurements are the evidence that it works as configured; these pages define what each feature does.

**L0 — ML model**

58. F. T. Liu, K. M. Ting, Z.-H. Zhou, *Isolation Forest*, IEEE ICDM 2008. The unsupervised safety-net
    model. https://doi.org/10.1109/ICDM.2008.17
59. T. Chen, C. Guestrin, *XGBoost: A Scalable Tree Boosting System*, ACM KDD 2016. The primary classifier.
    https://arxiv.org/abs/1603.02754
60. scikit-learn, *IsolationForest* (`contamination`, `n_estimators`).
    https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html
61. scikit-learn, *Model persistence*: a pickled model must be loaded by the library version that wrote it
    (why training and serving pin the same versions). https://scikit-learn.org/stable/model_persistence.html

**L1 — Serving API**

62. FastAPI documentation (routes, `Query`, `responses=`, dependency on Pydantic). https://fastapi.tiangolo.com/
63. Pydantic documentation (request models, `Field` bounds, `Literal`). https://docs.pydantic.dev/
64. Prometheus Python client (`Counter`, `Gauge`, `Histogram`, `/metrics`). https://github.com/prometheus/client_python
65. Psycopg 3 documentation (the prediction and feedback log in PostgreSQL). https://www.psycopg.org/psycopg3/docs/

**L2 — Container image**

66. Docker, *Dockerfile reference* (`USER` for the non-root user, `HEALTHCHECK`).
    https://docs.docker.com/reference/dockerfile/

**L3 — CI/CD**

67. SonarSource, *Pausing the pipeline until the quality gate is computed* (`withSonarQubeEnv`,
    `waitForQualityGate`, the webhook).
    https://docs.sonarsource.com/sonarqube-server/2026.3/analyzing-source-code/ci-integration/jenkins-integration/pipeline-pause
68. CycloneDX, the SBOM format produced in stage 6. https://cyclonedx.org/

**L4 — Kubernetes**

69. Kubernetes, *Horizontal Pod Autoscaling*. https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/
70. Kubernetes, *Network Policies*. https://kubernetes.io/docs/concepts/services-networking/network-policies/
71. Kubernetes, *StatefulSets* (PostgreSQL with `volumeClaimTemplates`).
    https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/
72. Kubernetes, *Configure liveness, readiness and startup probes*.
    https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/
73. Kubernetes, *Configure a security context* (`runAsNonRoot`, `readOnlyRootFilesystem`,
    `allowPrivilegeEscalation`, `capabilities`). https://kubernetes.io/docs/tasks/configure-pod-container/security-context/
74. Kubernetes, *Pod Security Standards* (the `restricted` profile, `seccompProfile`).
    https://kubernetes.io/docs/concepts/security/pod-security-standards/
75. minikube, *minikube start* (`--force`, after a Docker restart). https://minikube.sigs.k8s.io/docs/commands/start/
76. Docker, *docker network connect* (`--ip`: Jenkins pinned to 192.168.49.10 on the minikube network).
    https://docs.docker.com/reference/cli/docker/network/connect/

**L5 — Observability** (Prometheus, Alertmanager, Grafana and MLflow are in sections 3 and 4)

77. Prometheus, *Node exporter* (the host VM's CPU, memory, disk and load metrics).
    https://github.com/prometheus/node_exporter

**L6 — Ansible and the host**

78. Ansible, *Protecting sensitive data with Ansible Vault*. https://docs.ansible.com/ansible/latest/vault_guide/index.html
79. Ansible, *community.docker.docker_container module* (Grafana, MinIO and the other containers).
    https://docs.ansible.com/ansible/latest/collections/community/docker/docker_container_module.html
80. Ansible, *ansible.builtin.systemd_service module* (the control panel, MLflow and agent units).
    https://docs.ansible.com/ansible/latest/collections/ansible/builtin/systemd_service_module.html
81. systemd, *systemd.service* (`Restart=always`: why the panel's Stop and Fix go through `systemctl`).
    https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html
82. Linux man pages, *setfacl* (the POSIX ACL that gives Jenkins access to the minikube credentials
    instead of `chmod -R a+rwX`). https://man7.org/linux/man-pages/man1/setfacl.1.html

**Cited in L4: the Jenkins-to-Minikube image path** (recorded when the problem was hit; not re-checked on 25 October)

83. kubernetes/minikube issue #16293: `minikube image load` with the Docker driver failing from inside a
    container (connection refused on the host-loopback SSH port), the reason the pipeline loads images with
    `docker save | docker exec -i minikube docker load`. https://github.com/kubernetes/minikube/issues/16293
84. kubernetes/minikube issue #15260: the same nested-container access problem.
    https://github.com/kubernetes/minikube/issues/15260

**Alerting behaviour behind the anomaly list's design** (reproduced with the real Alertmanager on 25 October)

85. Prometheus source, `rules/alerting.go`: a resolved alert is kept and re-sent to Alertmanager for 15 minutes
    (`resolvedRetention`), every `--rules.alert.resend-delay` (1 minute by default).
    https://github.com/prometheus/prometheus/blob/main/rules/alerting.go
86. Alertmanager source, `notify/notify.go` (`DedupStage`): a group is notified again when its firing set gains an
    alert, or, with `send_resolved: true`, when its resolved set changes; why the list ignores resolved alerts.
    https://github.com/prometheus/alertmanager/blob/main/notify/notify.go

**Machines that miss their reports** (25 October)

87. Prometheus, *Template reference*: `query`, `first`, `value` and `humanizeDuration` in alert annotations
    (`MachineSilent` gives each machine's usual interval and last verdict).
    https://prometheus.io/docs/visualization/template_reference/
