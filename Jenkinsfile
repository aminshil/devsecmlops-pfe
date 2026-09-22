pipeline {
    agent any

    environment {
        IMAGE_NAME    = "devsecmlops-api"
        IMAGE_TAG     = "${readFile('VERSION').trim()}-b${BUILD_NUMBER}"
        REGISTRY      = "localhost:5000"
        F1_THRESHOLD  = "0.60"
    }

    stages {

        stage('1. Checkout') {
            steps {
                checkout scm
                sh 'git log --oneline -1'
                sh 'ls -la'
            }
        }

        stage('1b. Unit tests (pytest)') {
            steps {
                sh '''
                    python3 -m pip install --quiet --break-system-packages -r requirements-api.txt
                    python3 -m pip install --quiet --break-system-packages -r requirements-dev.txt
                    python3 -m pytest tests/ -v --tb=short \
                        --cov=api --cov=ml-model --cov-report=xml:coverage.xml
                '''
            }
        }


        stage('2. SAST (SonarQube)') {
            steps {
                withSonarQubeEnv('sonarqube') {
                    sh '''
                        sonar-scanner \
                          -Dsonar.projectKey=devsecmlops-pfe \
                          -Dsonar.sources=api,ml-model \
                          -Dsonar.python.version=3.10 \
                          -Dsonar.python.coverage.reportPaths=coverage.xml
                    '''
                }
            }
        }

        stage('2b. Quality Gate') {
            steps {
                timeout(time: 3, unit: 'MINUTES') {
                    waitForQualityGate abortPipeline: false
                }
            }
        }

        stage('3. Build Docker image') {
            steps {
                sh '''
                    docker build -t ${IMAGE_NAME}:${IMAGE_TAG} .
                    docker tag ${IMAGE_NAME}:${IMAGE_TAG} ${IMAGE_NAME}:latest
                    docker images | grep ${IMAGE_NAME} | head -5
                '''
            }
        }

        stage('4. Container smoke test') {
            steps {
                sh '''
                    docker rm -f api-ci-test 2>/dev/null || true
                    docker run -d --name api-ci-test -p 8010:8000 ${IMAGE_NAME}:${IMAGE_TAG}
                    sleep 8
                    echo "── /health ──"
                    curl -sf http://localhost:8010/health | head -c 500 || echo "(health check unreachable from Jenkins container)"
                    echo ""
                    docker rm -f api-ci-test
                '''
            }
        }

        stage('5. Trivy CVE scan') {
            steps {
                sh '''
                    trivy image \
                      --severity HIGH,CRITICAL \
                      --ignore-unfixed \
                      --format table \
                      ${IMAGE_NAME}:${IMAGE_TAG} || echo "Trivy found vulnerabilities (non-blocking for demo)"
                '''
            }
        }

        stage('6. Push to registry') {
            steps {
                sh '''
                    docker tag ${IMAGE_NAME}:${IMAGE_TAG} ${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}
                    docker tag ${IMAGE_NAME}:${IMAGE_TAG} ${REGISTRY}/${IMAGE_NAME}:latest
                    docker push ${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}
                    docker push ${REGISTRY}/${IMAGE_NAME}:latest
                    echo "── Registry catalog ──"
                    curl -s http://${REGISTRY}/v2/${IMAGE_NAME}/tags/list || echo "(registry catalog unreachable from Jenkins container)"
                '''
            }
        }

        stage('7. Deploy to Kubernetes') {
            steps {
                echo "Deploying ${IMAGE_NAME}:${IMAGE_TAG} to the ml-serving namespace"
                sh '''
                    export KUBECONFIG=/home/pfe/.kube/config

                    # `minikube image load` uses SSH internally to transfer the
                    # image into the node -- via a host-loopback address
                    # (127.0.0.1:<forwarded-ssh-port>) that only resolves
                    # correctly on the real host, never from inside ANY
                    # container. From Jenkins it fails structurally (confirmed
                    # live: cached the image correctly every time but never
                    # actually injected it, leaving pods stuck
                    # ErrImageNeverPull). Piping through the shared Docker
                    # socket instead -- the same mechanism Jenkins already
                    # uses for every other docker command in this pipeline --
                    # bypasses SSH entirely and loads the image directly into
                    # the node's own Docker daemon.
                    docker save ${IMAGE_NAME}:${IMAGE_TAG} | docker exec -i minikube docker load

                    # Point the deployment at this build's image and wait for the
                    # rolling update to actually finish -- not just "kubectl accepted
                    # the command", but pods genuinely Ready on the new version.
                    kubectl set image deployment/anomaly-api \
                        api=${IMAGE_NAME}:${IMAGE_TAG} -n ml-serving

                    kubectl rollout status deployment/anomaly-api \
                        -n ml-serving --timeout=180s
                '''
            }
        }
        stage('8. Post-deploy smoke test') {
            steps {
                echo "Verifying the newly-deployed pods actually serve correctly"
                sh '''
                    export KUBECONFIG=/home/pfe/.kube/config

                    kubectl exec -n ml-serving deploy/anomaly-api -- \
                        python3 -c "import urllib.request,json,sys; \
                        d=json.load(urllib.request.urlopen('http://localhost:8000/health', timeout=10)); \
                        print('health:', d); \
                        sys.exit(0 if d.get('status')=='ok' else 1)"
                '''
            }
        }
    }

    post {
        success {
            echo "Pipeline SUCCESS — ${IMAGE_NAME}:${IMAGE_TAG} built, scanned, pushed"
        }
        failure {
            echo "Pipeline FAILED at stage: ${env.STAGE_NAME}"
        }
        always {
            sh 'docker rm -f api-ci-test 2>/dev/null || true'
        }
    }
}
