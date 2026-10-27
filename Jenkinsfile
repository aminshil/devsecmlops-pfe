// DevSecMLOps application pipeline: every stage is a gate -- a failure
// stops the build before anything is pushed or deployed.
pipeline {
    agent any

    // One build at a time: two builds would race on the image tag, the registry's :latest and the
    // Deployment (the model job already has this; external review, 27 October).
    options {
        disableConcurrentBuilds()
    }

    environment {
        IMAGE_NAME    = "devsecmlops-api"
        IMAGE_TAG     = "${readFile('VERSION').trim()}-b${BUILD_NUMBER}"
        REGISTRY      = "localhost:5000"
        // Minimum served v4 F1 (independent test set) recorded in
        // models/manifest.json. Was already defined here but never
        // actually checked anywhere in the pipeline.
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
                          -Dsonar.sources=api,ml-model,monitoring,scripts \
                          -Dsonar.coverage.exclusions=monitoring/**,scripts/** \
                          -Dsonar.python.version=3.12 \
                          -Dsonar.python.coverage.reportPaths=coverage.xml
                    '''
                }
            }
        }

        stage('2b. Quality Gate') {
            steps {
                timeout(time: 3, unit: 'MINUTES') {
                    waitForQualityGate abortPipeline: true
                }
            }
        }

        stage('3. Repository scan + model gate') {
            steps {
                sh '''
                    # Dependencies declared in the repo, committed secrets, and
                    # IaC misconfigurations (Dockerfiles, Kubernetes manifests)
                    # -- separate from stage 6's scan of the BUILT IMAGE.
                    trivy fs --scanners vuln,secret,misconfig \
                      --severity HIGH,CRITICAL --exit-code 1 --timeout 15m \
                      --skip-dirs venv,.scannerwork,build,.git \
                      .

                    # The artifacts about to be baked into the image must be
                    # exactly the ones a real training run evaluated and
                    # promoted (SHA-256 match), with a recorded F1 at or
                    # above the floor -- not just "some manifest exists".
                    python3 scripts/verify_model_manifest.py --min-f1 ${F1_THRESHOLD}
                '''
            }
        }

        stage('4. Build Docker image') {
            steps {
                sh '''
                    docker build --pull --build-arg APT_REFRESH=${IMAGE_TAG} -t ${IMAGE_NAME}:${IMAGE_TAG} .
                    docker tag ${IMAGE_NAME}:${IMAGE_TAG} ${IMAGE_NAME}:latest
                    docker images | grep ${IMAGE_NAME} | head -5
                '''
            }
        }

        stage('5. Container smoke test') {
            steps {
                sh '''
                    # Run with the SAME restrictions the real pod runs under
                    # (non-root uid 10001, read-only root fs, no Linux
                    # capabilities) -- if the app needs something the
                    # production security context forbids, this is where
                    # that should be caught, not after a live deploy.
                    #
                    # On the shared Docker network Jenkins itself is on
                    # (devsecmlops-net), addressed BY NAME: "localhost" from
                    # inside the Jenkins container is Jenkins itself, never
                    # this test container -- confirmed live earlier, this is
                    # exactly why the previous version of this stage always
                    # silently no-op'd via "|| echo (unreachable)".
                    docker rm -f api-ci-test 2>/dev/null || true
                    docker run -d --name api-ci-test --network devsecmlops-net \
                      --read-only --tmpfs /tmp --cap-drop ALL \
                      --security-opt no-new-privileges --user 10001:10001 \
                      ${IMAGE_NAME}:${IMAGE_TAG}
                    python3 scripts/smoke_test.py --url http://api-ci-test:8000 \
                      --expect-version "$(cat VERSION)" --wait 90
                '''
            }
            post {
                always {
                    sh 'docker logs api-ci-test 2>&1 | tail -30 || true'
                    sh 'docker rm -f api-ci-test 2>/dev/null || true'
                }
            }
        }

        stage('6. Image scan + SBOM') {
            steps {
                sh '''
                    trivy image --severity HIGH,CRITICAL --ignore-unfixed \
                      --exit-code 1 --timeout 15m --format table ${IMAGE_NAME}:${IMAGE_TAG}
                    trivy image --format cyclonedx --timeout 15m --output sbom.cdx.json \
                      ${IMAGE_NAME}:${IMAGE_TAG}
                '''
                archiveArtifacts artifacts: 'sbom.cdx.json', fingerprint: true
            }
        }

        stage('7. Push to registry') {
            steps {
                sh '''
                    # Only the build's own tag here: :latest moves in stage 10, once this
                    # version is deployed and has passed its smoke test.
                    docker tag ${IMAGE_NAME}:${IMAGE_TAG} ${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}
                    docker push ${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}
                    echo "── Registry catalog ──"
                    curl -sf http://registry:5000/v2/${IMAGE_NAME}/tags/list
                '''
            }
        }

        stage('8. Deploy to Kubernetes') {
            steps {
                echo "Deploying ${IMAGE_NAME}:${IMAGE_TAG} to the ml-serving namespace"
                sh '''
                    export KUBECONFIG=/home/pfe/.kube/config

                    # `minikube image load` uses SSH internally to transfer the
                    # image into the node -- via a host-loopback address that
                    # only resolves correctly on the real host, never from
                    # inside any container. Piping through the shared Docker
                    # socket instead -- the same mechanism used for every
                    # other docker command in this pipeline -- bypasses SSH
                    # entirely and loads the image directly into the node's
                    # own Docker daemon.
                    docker save ${IMAGE_NAME}:${IMAGE_TAG} | docker exec -i minikube docker load

                    # The alert threshold comes from models/manifest.json inside the image.
                    # An old Deployment object may still carry a PREDICT_THRESHOLD value from
                    # before that (this stage only swaps the image, it never re-applies
                    # kubernetes/deployment.yaml), and an environment variable overrides the
                    # manifest. Removing it is a no-op when it is already gone.
                    kubectl set env deployment/anomaly-api PREDICT_THRESHOLD- -n ml-serving

                    kubectl set image deployment/anomaly-api \
                        api=${IMAGE_NAME}:${IMAGE_TAG} -n ml-serving

                    kubectl rollout status deployment/anomaly-api \
                        -n ml-serving --timeout=180s
                '''
            }
            post {
                failure {
                    // A rollout that does not finish (pods not Ready in 180 s) is put back, but only if
                    // this build had already set its image: a failure before `set image` (the image load)
                    // left the Deployment unchanged, and undoing then would roll back a good version.
                    sh '''
                        export KUBECONFIG=/home/pfe/.kube/config
                        CUR=$(kubectl get deployment/anomaly-api -n ml-serving \
                              -o jsonpath='{.spec.template.spec.containers[?(@.name=="api")].image}')
                        if [ "$CUR" = "${IMAGE_NAME}:${IMAGE_TAG}" ]; then
                            echo "Rollout of ${IMAGE_TAG} did not finish -- rolling back to the previous revision"
                            kubectl rollout undo deployment/anomaly-api -n ml-serving
                            kubectl rollout status deployment/anomaly-api -n ml-serving --timeout=180s
                        else
                            echo "Deployment still runs $CUR: nothing to roll back"
                        fi
                    '''
                }
            }
        }

        stage('9. Post-deploy smoke test') {
            steps {
                echo "Verifying the newly-deployed pods actually serve correctly"
                sh '''
                    export KUBECONFIG=/home/pfe/.kube/config

                    # Runs inside one of the new pods and calls it on
                    # http://localhost:8000 (the pod itself, not the Service):
                    # health, version, a v3 AND a v4 prediction, metrics, and
                    # confirms the ops UI is not exposed on this deployment.
                    kubectl exec -i -n ml-serving deploy/anomaly-api -c api -- \
                      python3 - --url http://localhost:8000 \
                      --expect-version "$(cat VERSION)" --wait 60 < scripts/smoke_test.py
                '''
            }
            post {
                failure {
                    // The new version is already live when this stage runs. If it fails
                    // its own smoke test, put the previous ReplicaSet back.
                    echo "Smoke test FAILED -- rolling the deployment back to the previous revision"
                    sh '''
                        export KUBECONFIG=/home/pfe/.kube/config
                        kubectl rollout undo deployment/anomaly-api -n ml-serving
                        kubectl rollout status deployment/anomaly-api -n ml-serving --timeout=180s
                    '''
                }
            }
        }

        stage('10. Tag latest') {
            steps {
                // Only a version that is deployed and passed its smoke test becomes :latest.
                sh '''
                    docker tag ${IMAGE_NAME}:${IMAGE_TAG} ${REGISTRY}/${IMAGE_NAME}:latest
                    docker push ${REGISTRY}/${IMAGE_NAME}:latest
                '''
            }
        }
    }

    post {
        success {
            echo "Pipeline SUCCESS — ${IMAGE_NAME}:${IMAGE_TAG} built, scanned, pushed, deployed, verified"
        }
        failure {
            echo "Pipeline FAILED at stage: ${env.STAGE_NAME}. Stages 1-7 deploy nothing. If it failed at stage 8 the rollout did not finish and, if the new image was already set, it was rolled back; if at stage 9 the new version was live and was rolled back automatically; at stage 10 only the registry's :latest tag was not moved."
        }
        always {
            sh 'docker rm -f api-ci-test 2>/dev/null || true'
        }
    }
}
