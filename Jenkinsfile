// ── dbt-coverage-lib · Jenkins Declarative Pipeline ──────────────────────────
//
// REQUIRED Jenkins setup (one-time):
//
//  1. Credentials (Manage Jenkins → Credentials → Global):
//     ID: jfrog-credentials   Type: Username with password
//         username = JFrog username or email
//         password = JFrog API key / identity token
//
//     ID: kubeconfig-file     Type: Secret file
//         file   = kubeconfig for the target cluster (base64 NOT needed — raw YAML)
//
//  2. Plugins: Pipeline, Docker Pipeline, JUnit, Cobertura (or Coverage),
//              Credentials Binding, Kubernetes CLI (optional)
//
//  3. Agent: the Jenkins agent that runs this pipeline needs:
//     - python3.11+, pip, build, twine  (or use Docker agent)
//     - docker CLI + daemon access (or Docker-in-Docker sidecar)
//     - kubectl in PATH
//
// ─────────────────────────────────────────────────────────────────────────────

pipeline {

    // ── Run on any available agent ─────────────────────────────────────────
    // Swap to `agent { docker { image 'python:3.11-slim' } }` for a clean
    // Python environment, but then Docker-in-Docker setup is needed for the
    // Docker build stages.
    agent any

    // ── Pipeline-wide environment variables ────────────────────────────────
    // Customise the four JFROG_* vars and K8S_NAMESPACE for your environment.
    environment {
        // ── JFrog ──────────────────────────────────────────────────────────
        JFROG_HOST        = "mycompany.jfrog.io"              // e.g. acme.jfrog.io
        JFROG_PYPI_REPO   = "dbt-coverage-pypi-local"        // Artifactory PyPI local/virtual repo name
        JFROG_DOCKER_REPO = "dbt-coverage-docker"            // Artifactory Docker local repo name

        // ── Kubernetes ─────────────────────────────────────────────────────
        K8S_NAMESPACE     = "dbt-coverage"

        // ── Derived (do not edit) ──────────────────────────────────────────
        IMAGE_NAME        = "${JFROG_HOST}/${JFROG_DOCKER_REPO}/dbt-coverage"
        PYPI_URL          = "https://${JFROG_HOST}/artifactory/api/pypi/${JFROG_PYPI_REPO}"
    }

    options {
        timestamps()
        timeout(time: 30, unit: "MINUTES")
        buildDiscarder(logRotator(numToKeepStr: "20"))
        disableConcurrentBuilds()
    }

    stages {

        // ── 1. Resolve version from pyproject.toml ─────────────────────────
        stage("Version") {
            steps {
                script {
                    // Python one-liner — no extra deps needed
                    env.PKG_VERSION = sh(
                        script: '''python3 -c "
import re, pathlib
txt = pathlib.Path('pyproject.toml').read_text()
print(re.search(r'^version\\s*=\\s*\\"([^\\"]+)\\"', txt, re.M).group(1))
"''',
                        returnStdout: true
                    ).trim()
                    echo "Package version: ${env.PKG_VERSION}"
                    env.IMAGE_TAG = "${IMAGE_NAME}:${env.PKG_VERSION}"
                }
            }
        }

        // ── 2. Install dependencies ────────────────────────────────────────
        stage("Install") {
            steps {
                sh """
                    python3 -m venv .ci-venv
                    . .ci-venv/bin/activate
                    pip install --quiet --upgrade pip
                    pip install --quiet -e '.[dev,ui]'
                    pip install --quiet build twine
                """
            }
        }

        // ── 3. Lint ────────────────────────────────────────────────────────
        stage("Lint") {
            steps {
                sh """
                    . .ci-venv/bin/activate
                    ruff check src/ tests/
                    mypy src/dbt_coverage --ignore-missing-imports --no-error-summary
                """
            }
        }

        // ── 4. Test ────────────────────────────────────────────────────────
        stage("Test") {
            steps {
                sh """
                    . .ci-venv/bin/activate
                    pytest tests/ \
                        --junit-xml=test-results.xml \
                        --cov=dbt_coverage \
                        --cov-report=xml:coverage.xml \
                        --cov-report=term-missing \
                        -q
                """
            }
            post {
                always {
                    junit "test-results.xml"
                    // Requires the 'Coverage' or 'Cobertura' plugin:
                    recordCoverage(
                        tools: [[parser: "COBERTURA", pattern: "coverage.xml"]],
                        id: "coverage",
                        name: "Python Coverage"
                    )
                }
            }
        }

        // ── 5. dbtcov — Scan & Gate ────────────────────────────────────────
        //
        //  --fail-on tier-1   non-zero exit on any Tier-1 finding (blocks merge)
        //  --baseline         suppress pre-existing findings recorded on main so
        //                     only *new* problems introduced by this change fail
        //  --format sarif     machine-readable for downstream tooling / archiving
        //  --format json      findings.json + coverage.json artefacts
        //
        //  On the main branch the baseline is refreshed after a successful gate
        //  so future PRs only fail on genuinely new findings.
        //
        //  Adjust DBT_PROJECT_PATH if your dbt project lives in a subdirectory.
        // ─────────────────────────────────────────────────────────────────────
        stage("dbtcov Scan & Gate") {
            environment {
                DBT_PROJECT_PATH = "."
                DBTCOV_BASELINE  = ".dbtcov/baseline.json"
            }
            steps {
                sh """
                    . .ci-venv/bin/activate
                    dbtcov scan "${DBT_PROJECT_PATH}" \
                        --fail-on tier-1 \
                        --baseline "${DBTCOV_BASELINE}" \
                        --format sarif \
                        --format json \
                        --format console \
                        --out dbtcov-out
                """
            }
            post {
                always {
                    // Archive the full report directory so findings are always
                    // accessible even when the gate fails.
                    archiveArtifacts(
                        artifacts: "dbtcov-out/**",
                        fingerprint: true,
                        allowEmptyArchive: true
                    )
                }
            }
        }

        // ── 5a. Refresh baseline (main branch only, after gate passes) ─────
        stage("dbtcov Baseline Capture") {
            when { branch "main" }
            environment {
                DBT_PROJECT_PATH = "."
                DBTCOV_BASELINE  = ".dbtcov/baseline.json"
            }
            steps {
                sh """
                    . .ci-venv/bin/activate
                    dbtcov baseline capture \
                        --path "${DBT_PROJECT_PATH}" \
                        --out  "${DBTCOV_BASELINE}"
                """
                // Commit the updated baseline back to main so future PRs get
                // the correct suppression window.  Requires the Jenkins agent
                // to have git push access (SSH key or credentials helper).
                sh """
                    git config user.email "jenkins@ci"
                    git config user.name  "Jenkins"
                    git add "${DBTCOV_BASELINE}"
                    git diff --cached --quiet \\
                        || git commit -m "chore(dbtcov): update baseline [skip ci]"
                    git push origin HEAD:main
                """
            }
        }

        // ── 7. Build wheel ─────────────────────────────────────────────────
        stage("Build Wheel") {
            steps {
                sh """
                    . .ci-venv/bin/activate
                    python -m build --wheel --sdist --outdir dist/
                """
                archiveArtifacts artifacts: "dist/**", fingerprint: true
            }
        }

        // ── The following stages run on main branch only ───────────────────
        // ── 8. Publish Python package to JFrog Artifactory PyPI ───────────
        stage("Publish PyPI → JFrog") {
            when { branch "main" }
            steps {
                withCredentials([usernamePassword(
                    credentialsId: "jfrog-credentials",
                    usernameVariable: "JFROG_USER",
                    passwordVariable: "JFROG_PASS"
                )]) {
                    sh """
                        . .ci-venv/bin/activate
                        twine upload \
                            --repository-url "${PYPI_URL}" \
                            --username "${JFROG_USER}" \
                            --password "${JFROG_PASS}" \
                            --non-interactive \
                            --skip-existing \
                            dist/*
                    """
                }
            }
        }

        // ── 9. Build Docker image ──────────────────────────────────────────
        stage("Build Docker") {
            when { branch "main" }
            steps {
                sh "docker build --pull -t ${IMAGE_TAG} -t ${IMAGE_NAME}:latest ."
            }
        }

        // ── 10. Push Docker image to JFrog Artifactory ─────────────────────
        stage("Push Docker → JFrog") {
            when { branch "main" }
            steps {
                withCredentials([usernamePassword(
                    credentialsId: "jfrog-credentials",
                    usernameVariable: "JFROG_USER",
                    passwordVariable: "JFROG_PASS"
                )]) {
                    sh """
                        echo "${JFROG_PASS}" | docker login "${JFROG_HOST}" \
                            --username "${JFROG_USER}" --password-stdin
                        docker push "${IMAGE_TAG}"
                        docker push "${IMAGE_NAME}:latest"
                        docker logout "${JFROG_HOST}"
                    """
                }
            }
        }

        // ── 11. Deploy to Kubernetes ────────────────────────────────────────
        stage("Deploy → Kubernetes") {
            when { branch "main" }
            steps {
                withCredentials([file(
                    credentialsId: "kubeconfig-file",
                    variable: "KUBECONFIG"
                )]) {
                    sh """
                        # Ensure namespace exists
                        kubectl get namespace ${K8S_NAMESPACE} \
                            || kubectl create namespace ${K8S_NAMESPACE}

                        # Apply namespace-scoped manifests
                        kubectl apply -f k8s/deployment.yaml -n ${K8S_NAMESPACE}
                        kubectl apply -f k8s/service.yaml    -n ${K8S_NAMESPACE}
                        kubectl apply -f k8s/ingress.yaml    -n ${K8S_NAMESPACE}

                        # Swap the image tag to the newly built version
                        kubectl set image deployment/dbt-coverage-ui \
                            dbt-coverage-ui=${IMAGE_TAG} \
                            -n ${K8S_NAMESPACE}

                        # Wait for rollout (max 5 min)
                        kubectl rollout status deployment/dbt-coverage-ui \
                            -n ${K8S_NAMESPACE} --timeout=300s
                    """
                }
            }
        }
    }

    // ── Post-pipeline notifications ────────────────────────────────────────
    post {
        success {
            echo "Pipeline succeeded — ${env.PKG_VERSION} published & deployed."
        }
        failure {
            echo "Pipeline FAILED — check the stage logs above."
        }
        cleanup {
            // Remove local Docker image to keep agent disk clean
            sh "docker rmi ${IMAGE_TAG} ${IMAGE_NAME}:latest || true"
            // Remove venv to keep workspace tidy
            sh "rm -rf .ci-venv dist/"
        }
    }
}
