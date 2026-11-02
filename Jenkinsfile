// FinSecure Pay - CI/CD pipeline as code.
//
// Checkout -> Secret Scan -> Build -> Lint -> Test -> Package -> Security Checks
//   -> Container Build -> Image Scan -> Push -> Deploy (EKS) -> Smoke Test
//
// Every gate fails the build and stops everything after it, so a failing test,
// a leaked secret or a HIGH/CRITICAL vulnerability can never reach the cluster.
// A deployment that does not become healthy is rolled back automatically.
//
// Credentials (Manage Jenkins > Credentials, IDs below) - never stored in Git:
//   dockerhub-credentials  username + Docker Hub access token
//   aws-deployer           username = AWS access key ID, password = secret key
//   finsecure-api-keys     secret text, API key(s) clients must send
//   sonar-token            secret text (used by the "sonarqube" server config)
//   anthropic-api-key      secret text, OPTIONAL - enables LLM failure analysis

def IMAGE_TAG = ''
def APP_VERSION = ''
def ROLLED_BACK = false
def RCA_SUMMARY = ''

pipeline {
    agent any

    options {
        timestamps()
        ansiColor('xterm')
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '30', artifactNumToKeepStr: '10'))
        timeout(time: 45, unit: 'MINUTES')
    }

    // Poll GitHub every ~2 minutes; a push to main triggers the pipeline.
    // (Add a GitHub webhook as well if Jenkins is reachable from the internet.)
    triggers {
        pollSCM('H/2 * * * *')
    }

    parameters {
        choice(name: 'ACTION', choices: ['build-and-deploy', 'rollback'],
               description: 'build-and-deploy: full pipeline. rollback: return the cluster to a known-good revision.')
        string(name: 'ROLLBACK_REVISION', defaultValue: '',
               description: 'Rollback only: Deployment revision from `kubectl rollout history` (empty = previous revision).')
    }

    environment {
        APP_NAME        = 'finsecure-pay'
        DOCKERHUB_USER  = 'kelvinsoe007'                       // change to your Docker Hub user
        IMAGE_REPO      = "docker.io/${DOCKERHUB_USER}/${APP_NAME}"
        AWS_REGION      = 'ap-southeast-1'
        EKS_CLUSTER     = 'finsecure-eks'
        K8S_NAMESPACE   = 'finsecure'
        KUBECONFIG      = "${WORKSPACE}/.kube/config"
        REPORTS         = 'reports'
        VENV            = '.venv'
        UV_NO_PROGRESS  = '1'
    }

    stages {
        // =============================================================== rollback
        stage('Rollback') {
            when { expression { params.ACTION == 'rollback' } }
            steps {
                withCredentials([usernamePassword(credentialsId: 'aws-deployer',
                        usernameVariable: 'AWS_ACCESS_KEY_ID', passwordVariable: 'AWS_SECRET_ACCESS_KEY')]) {
                    sh '''
                        aws eks update-kubeconfig --name "$EKS_CLUSTER" --region "$AWS_REGION" --kubeconfig "$KUBECONFIG"
                        echo "--- history before rollback"
                        kubectl -n "$K8S_NAMESPACE" rollout history deployment/"$APP_NAME"
                        if [ -n "$ROLLBACK_REVISION" ]; then
                            kubectl -n "$K8S_NAMESPACE" rollout undo deployment/"$APP_NAME" --to-revision="$ROLLBACK_REVISION"
                        else
                            kubectl -n "$K8S_NAMESPACE" rollout undo deployment/"$APP_NAME"
                        fi
                        kubectl -n "$K8S_NAMESPACE" annotate deployment/"$APP_NAME" --overwrite \
                            kubernetes.io/change-cause="Jenkins #$BUILD_NUMBER: manual rollback ${ROLLBACK_REVISION:+to revision $ROLLBACK_REVISION}"
                        kubectl -n "$K8S_NAMESPACE" rollout status deployment/"$APP_NAME" --timeout=180s
                        echo "--- history after rollback"
                        kubectl -n "$K8S_NAMESPACE" rollout history deployment/"$APP_NAME"
                        echo "--- now running"
                        kubectl -n "$K8S_NAMESPACE" get deployment "$APP_NAME" \
                            -o jsonpath='{.spec.template.spec.containers[0].image}{"\\n"}'
                    '''
                }
                script { ROLLED_BACK = true }
            }
        }

        // =============================================================== CI
        stage('Checkout') {
            when { expression { params.ACTION == 'build-and-deploy' } }
            steps {
                checkout scm
                script {
                    // app/__init__.py only defines __version__, so this needs no dependencies.
                    APP_VERSION = sh(returnStdout: true,
                        script: 'python3 -c "import app; print(app.__version__)"').trim()
                    def shortSha = sh(returnStdout: true, script: 'git rev-parse --short=7 HEAD').trim()
                    IMAGE_TAG = "${APP_VERSION}-${env.BUILD_NUMBER}-${shortSha}"
                    currentBuild.displayName = "#${env.BUILD_NUMBER} ${IMAGE_TAG}"
                }
                sh 'mkdir -p "$REPORTS"'
            }
        }

        stage('Secret Scan') {
            when { expression { params.ACTION == 'build-and-deploy' } }
            steps {
                // Fails the build if any credential, key or token is found in Git history.
                sh 'gitleaks git --redact --verbose --report-format sarif --report-path "$REPORTS/gitleaks.sarif" .'
            }
            post {
                always { recordIssues(tools: [sarif(id: 'gitleaks', name: 'Gitleaks', pattern: 'reports/gitleaks.sarif')], skipPublishingChecks: true) }
            }
        }

        stage('Build') {
            when { expression { params.ACTION == 'build-and-deploy' } }
            steps {
                // Python 3.11 = same interpreter as the distroless runtime image.
                sh '''
                    uv venv --python 3.11 "$VENV"
                    uv pip install --python "$VENV/bin/python" --require-hashes -r requirements.txt -r requirements-dev.txt
                    "$VENV/bin/python" --version
                '''
            }
        }

        stage('Lint') {
            when { expression { params.ACTION == 'build-and-deploy' } }
            steps {
                sh '''
                    "$VENV/bin/ruff" check . --output-format=full
                    "$VENV/bin/ruff" format --check .
                '''
            }
        }

        stage('Unit & Integration Tests') {
            when { expression { params.ACTION == 'build-and-deploy' } }
            steps {
                // Fails on any test failure or if coverage drops below 90%.
                sh '''
                    "$VENV/bin/pytest" \
                        --junitxml="$REPORTS/junit.xml" \
                        --cov --cov-report=term --cov-report=xml:"$REPORTS/coverage.xml" \
                        --cov-report=html:"$REPORTS/htmlcov"
                    "$VENV/bin/pytest" ai/tests --junitxml="$REPORTS/junit-ai.xml" -q
                '''
            }
            post {
                always {
                    junit testResults: 'reports/junit*.xml', allowEmptyResults: false
                    recordCoverage(tools: [[parser: 'COBERTURA', pattern: 'reports/coverage.xml']],
                                   sourceCodeRetention: 'EVERY_BUILD')
                }
            }
        }

        stage('Package') {
            when { expression { params.ACTION == 'build-and-deploy' } }
            steps {
                sh '"$VENV/bin/python" -m build --wheel --outdir dist .'
                archiveArtifacts artifacts: 'dist/*.whl', fingerprint: true
            }
        }

        stage('Security Checks') {
            when { expression { params.ACTION == 'build-and-deploy' } }
            parallel {
                stage('SAST - Bandit') {
                    steps {
                        sh '''
                            "$VENV/bin/bandit" -c pyproject.toml -r app -f sarif -o "$REPORTS/bandit.sarif" --exit-zero
                            # Gate: any HIGH severity finding fails the build.
                            "$VENV/bin/bandit" -c pyproject.toml -r app --severity-level high --confidence-level medium
                        '''
                    }
                }
                stage('SAST - SonarQube') {
                    steps {
                        withSonarQubeEnv('sonarqube') {
                            // sonar.qualitygate.wait makes the scanner itself fail when the
                            // quality gate fails (no inbound webhook to Jenkins needed).
                            sh '''
                                ORG_ARG=""
                                [ -n "${SONAR_ORGANIZATION:-}" ] && ORG_ARG="-Dsonar.organization=$SONAR_ORGANIZATION"
                                sonar-scanner $ORG_ARG \
                                    -Dsonar.projectVersion="$APP_VERSION_ENV" \
                                    -Dsonar.qualitygate.wait=true \
                                    -Dsonar.qualitygate.timeout=300
                            '''
                        }
                    }
                    environment { APP_VERSION_ENV = "${APP_VERSION}" }
                }
                stage('Dependency Scan') {
                    steps {
                        sh '''
                            # Runtime lock file only: dev tools never ship in the image.
                            # Trivy is fed plain name==version pins (pip-compile hash lines stripped)
                            # so every locked package is guaranteed to be parsed.
                            mkdir -p "$REPORTS/deps"
                            sed -n 's/^\\([A-Za-z0-9._-]*==[^ ]*\\).*/\\1/p' requirements.txt > "$REPORTS/deps/requirements.txt"
                            echo "Scanning $(wc -l < "$REPORTS/deps/requirements.txt") locked runtime packages"
                            trivy fs --scanners vuln --format sarif --output "$REPORTS/trivy-deps.sarif" "$REPORTS/deps"
                            # Gate: HIGH or CRITICAL CVE in any runtime dependency fails the build.
                            trivy fs --scanners vuln --severity HIGH,CRITICAL --exit-code 1 "$REPORTS/deps"
                            # Second opinion from the PyPA advisory database (report only).
                            "$VENV/bin/pip-audit" -r requirements.txt --require-hashes --disable-pip \
                                --format json --output "$REPORTS/pip-audit.json" || true
                        '''
                    }
                }
                stage('IaC / Config Scan') {
                    steps {
                        sh '''
                            TRIVY_SKIP="--skip-dirs .venv,dist,reports,.git"
                            trivy config $TRIVY_SKIP --format sarif --output "$REPORTS/trivy-config.sarif" .
                            # Gate: CRITICAL misconfiguration in the Dockerfile or Kubernetes manifests.
                            trivy config $TRIVY_SKIP --severity CRITICAL --exit-code 1 .
                        '''
                    }
                }
            }
            post {
                always {
                    recordIssues(tools: [
                        sarif(id: 'bandit', name: 'Bandit', pattern: 'reports/bandit.sarif'),
                        sarif(id: 'trivy-deps', name: 'Trivy dependencies', pattern: 'reports/trivy-deps.sarif'),
                        sarif(id: 'trivy-config', name: 'Trivy IaC', pattern: 'reports/trivy-config.sarif')
                    ], skipPublishingChecks: true)
                }
            }
        }

        stage('Container Build') {
            when { expression { params.ACTION == 'build-and-deploy' } }
            steps {
                sh """
                    docker build \
                        --build-arg VERSION=${IMAGE_TAG} \
                        --build-arg GIT_COMMIT="\$(git rev-parse HEAD)" \
                        --build-arg BUILD_DATE="\$(git log -1 --format=%cI)" \
                        --build-arg SOURCE_DATE_EPOCH="\$(git log -1 --format=%ct)" \
                        --tag ${IMAGE_REPO}:${IMAGE_TAG} .
                    docker image inspect ${IMAGE_REPO}:${IMAGE_TAG} --format 'Image size: {{.Size}} bytes, user: {{.Config.User}}'
                """
            }
        }

        stage('Image Scan') {
            when { expression { params.ACTION == 'build-and-deploy' } }
            steps {
                sh """
                    trivy image --format sarif --output "\$REPORTS/trivy-image.sarif" ${IMAGE_REPO}:${IMAGE_TAG}
                    # Software Bill of Materials for the shipped image (CycloneDX)
                    trivy image --format cyclonedx --output "\$REPORTS/sbom.cdx.json" ${IMAGE_REPO}:${IMAGE_TAG}
                    # Gate: HIGH or CRITICAL vulnerability with an available fix blocks the release.
                    trivy image --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 ${IMAGE_REPO}:${IMAGE_TAG}
                """
            }
            post {
                always { recordIssues(tools: [sarif(id: 'trivy-image', name: 'Trivy image', pattern: 'reports/trivy-image.sarif')], skipPublishingChecks: true) }
            }
        }

        // =============================================================== CD
        stage('Push') {
            when {
                allOf {
                    expression { params.ACTION == 'build-and-deploy' }
                    expression { (env.GIT_BRANCH ?: '') ==~ /(origin\/)?main/ }
                }
            }
            steps {
                withCredentials([usernamePassword(credentialsId: 'dockerhub-credentials',
                        usernameVariable: 'REGISTRY_USER', passwordVariable: 'REGISTRY_TOKEN')]) {
                    sh """
                        echo "\$REGISTRY_TOKEN" | docker login docker.io -u "\$REGISTRY_USER" --password-stdin
                        docker push ${IMAGE_REPO}:${IMAGE_TAG}
                        # Also publish the semantic version tag when this commit is a release tag (vX.Y.Z).
                        if git describe --exact-match --tags HEAD 2>/dev/null | grep -q '^v'; then
                            docker tag ${IMAGE_REPO}:${IMAGE_TAG} ${IMAGE_REPO}:${APP_VERSION}
                            docker push ${IMAGE_REPO}:${APP_VERSION}
                        fi
                        docker logout docker.io
                    """
                }
            }
        }

        stage('Deploy to EKS') {
            when {
                allOf {
                    expression { params.ACTION == 'build-and-deploy' }
                    expression { (env.GIT_BRANCH ?: '') ==~ /(origin\/)?main/ }
                }
            }
            steps {
                withCredentials([
                    usernamePassword(credentialsId: 'aws-deployer',
                        usernameVariable: 'AWS_ACCESS_KEY_ID', passwordVariable: 'AWS_SECRET_ACCESS_KEY'),
                    string(credentialsId: 'finsecure-api-keys', variable: 'API_KEYS')
                ]) {
                    script {
                        def status = sh(returnStatus: true, script: """
                            set -e
                            aws eks update-kubeconfig --name "\$EKS_CLUSTER" --region "\$AWS_REGION" --kubeconfig "\$KUBECONFIG"

                            # Application secret: created from the Jenkins credential, never from Git.
                            kubectl -n "\$K8S_NAMESPACE" create secret generic finsecure-pay-secrets \
                                --from-literal=API_KEYS="\$API_KEYS" --dry-run=client -o yaml | kubectl apply -f -

                            cd k8s/overlays/eks
                            kustomize edit set image finsecure-pay=${IMAGE_REPO}:${IMAGE_TAG}
                            cd -
                            kubectl apply -k k8s/overlays/eks
                            kubectl -n "\$K8S_NAMESPACE" annotate deployment/"\$APP_NAME" --overwrite \
                                kubernetes.io/change-cause="Jenkins #${env.BUILD_NUMBER}: deploy ${IMAGE_TAG}"

                            # Wait for the new pods to pass readiness; fails after 180s.
                            kubectl -n "\$K8S_NAMESPACE" rollout status deployment/"\$APP_NAME" --timeout=180s
                        """)
                        if (status != 0) {
                            echo 'Rollout did not become healthy - rolling back automatically'
                            rollbackDeployment()
                            ROLLED_BACK = true
                            error('Deployment failed health checks and was rolled back to the previous revision')
                        }
                    }
                    sh 'kubectl -n "$K8S_NAMESPACE" get deploy,rs,pods,hpa,ingress -o wide'
                }
            }
        }

        stage('Smoke Test') {
            when {
                allOf {
                    expression { params.ACTION == 'build-and-deploy' }
                    expression { (env.GIT_BRANCH ?: '') ==~ /(origin\/)?main/ }
                }
            }
            steps {
                withCredentials([
                    usernamePassword(credentialsId: 'aws-deployer',
                        usernameVariable: 'AWS_ACCESS_KEY_ID', passwordVariable: 'AWS_SECRET_ACCESS_KEY'),
                    string(credentialsId: 'finsecure-api-keys', variable: 'API_KEYS')
                ]) {
                    script {
                        def status = sh(returnStatus: true, script: '''
                            HOST=$(kubectl -n "$K8S_NAMESPACE" get ingress "$APP_NAME" \
                                -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
                            scripts/smoke-test.sh "http://$HOST" "${API_KEYS%%,*}"
                        ''')
                        if (status != 0) {
                            echo 'Smoke test failed against the live service - rolling back'
                            rollbackDeployment()
                            ROLLED_BACK = true
                            error('Smoke test failed; previous version restored')
                        }
                    }
                }
            }
        }
    }

    post {
        always {
            archiveArtifacts artifacts: 'reports/**', allowEmptyArchive: true
        }
        success {
            script {
                // Only e-mail successes that changed the cluster (deploys from main, rollbacks).
                if (params.ACTION == 'rollback' || (env.GIT_BRANCH ?: '') ==~ /(origin\/)?main/) {
                    notify(ROLLED_BACK, '', IMAGE_TAG)
                }
            }
        }
        failure {
            script {
                RCA_SUMMARY = runFailureAnalysis()
                notify(ROLLED_BACK, RCA_SUMMARY, IMAGE_TAG)
            }
        }
        unstable {
            script { notify(ROLLED_BACK, '', IMAGE_TAG) }
        }
        cleanup {
            sh "docker image rm -f '${env.IMAGE_REPO}:${IMAGE_TAG}' >/dev/null 2>&1 || true"
        }
    }
}

// ------------------------------------------------------------------ helpers

def rollbackDeployment() {
    sh '''
        set +e
        kubectl -n "$K8S_NAMESPACE" rollout undo deployment/"$APP_NAME" \
            && kubectl -n "$K8S_NAMESPACE" annotate deployment/"$APP_NAME" --overwrite \
                kubernetes.io/change-cause="Jenkins #$BUILD_NUMBER: automatic rollback (new version unhealthy)" \
            && kubectl -n "$K8S_NAMESPACE" rollout status deployment/"$APP_NAME" --timeout=180s
        kubectl -n "$K8S_NAMESPACE" rollout history deployment/"$APP_NAME" | tail -5
        kubectl -n "$K8S_NAMESPACE" get pods -o wide
    '''
}

// AI-assisted root-cause analysis of the failed build (see ai/README.md).
// Uses an LLM when the optional anthropic-api-key credential exists; otherwise
// falls back to deterministic rule-based classification. Advisory only.
def runFailureAnalysis() {
    try {
        writeFile file: 'reports/console.log', text: currentBuild.rawBuild.getLog(3000).join('\n')
        def cmd = 'python3 ai/rca/analyze.py --log reports/console.log --output reports/rca.md --json reports/rca.json'
        try {
            withCredentials([string(credentialsId: 'anthropic-api-key', variable: 'ANTHROPIC_API_KEY')]) {
                sh cmd
            }
        } catch (ignored) {
            sh cmd   // credential not configured: rule-based analysis only
        }
        archiveArtifacts artifacts: 'reports/rca.*', allowEmptyArchive: true
        return readFile('reports/rca.md')
    } catch (err) {
        echo "Failure analysis skipped: ${err}"
        return ''
    }
}

// HTML e-mail via the Email Extension plugin (SMTP configured in jenkins/casc.yaml).
def notify(boolean rolledBack, String rca, String imageTag) {
    def result = currentBuild.currentResult
    def colour = result == 'SUCCESS' ? '#1a7f37' : '#cf222e'
    def rcaHtml = rca ? "<h3>AI failure analysis (advisory)</h3><pre style='white-space:pre-wrap'>${rca.replace('<', '&lt;')}</pre>" : ''
    emailext(
        to: env.NOTIFY_EMAIL,
        subject: "[FinSecure Pay] ${result}: ${env.JOB_NAME} #${env.BUILD_NUMBER}" + (rolledBack ? ' (ROLLED BACK)' : ''),
        mimeType: 'text/html',
        attachLog: result != 'SUCCESS',
        compressLog: true,
        body: """
            <h2 style='color:${colour}'>${result}</h2>
            <table cellpadding='4'>
              <tr><td><b>Job</b></td><td>${env.JOB_NAME} #${env.BUILD_NUMBER}</td></tr>
              <tr><td><b>Action</b></td><td>${params.ACTION}</td></tr>
              <tr><td><b>Image</b></td><td>${env.IMAGE_REPO}:${imageTag ?: 'n/a'}</td></tr>
              <tr><td><b>Commit</b></td><td>${env.GIT_COMMIT ?: 'n/a'}</td></tr>
              <tr><td><b>Rolled back</b></td><td>${rolledBack ? 'YES - previous version restored' : 'no'}</td></tr>
              <tr><td><b>Duration</b></td><td>${currentBuild.durationString}</td></tr>
            </table>
            <p><a href='${env.BUILD_URL}'>Open build</a> |
               <a href='${env.BUILD_URL}console'>Console log</a> |
               <a href='${env.BUILD_URL}testReport'>Tests</a></p>
            ${rcaHtml}
        """
    )
}
