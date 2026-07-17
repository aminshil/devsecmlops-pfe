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
                    python3 -m pytest tests/ -v --tb=short
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
                          -Dsonar.python.version=3.10
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

        stage('7. Deploy (K8s placeholder)') {
            steps {
                echo "Deploy stage — will apply K8s manifests in L4"
                echo "For now: verify image is pullable from registry"
                sh '''
                    docker rmi ${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG} || true
                    docker pull ${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}
                    echo "Pipeline complete — image ${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG} ready for K8s deployment"
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
