{
  description = "YAMAI protocol verification environment (Quint, TLC, and Z3)";

  inputs = {
    # The unstable branch is used for Linux and Apple Silicon macOS.
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    # nixpkgs 26.11 dropped x86_64-darwin; keep Intel macOS reproducible
    # on the supported 26.05 Darwin branch until its security-support window ends.
    nixpkgsDarwin.url = "github:NixOS/nixpkgs/nixpkgs-26.05-darwin";
  };

  outputs =
    { self, nixpkgs, nixpkgsDarwin }:
    let
      systems = [
        "aarch64-darwin"
        "x86_64-darwin"
        "aarch64-linux"
        "x86_64-linux"
      ];

      forEachSystem =
        f:
        nixpkgs.lib.genAttrs systems (
          system:
          f {
            inherit system;
            pkgs = import (
              if system == "x86_64-darwin" then nixpkgsDarwin else nixpkgs
            ) { inherit system; };
          }
        );

      checkerPackages =
        pkgs: [
          pkgs.quint.out
          pkgs.tlaplus.out
          pkgs.jre_headless.out
          pkgs.z3.out
          pkgs.jq.out
        ];
    in
    {
      formatter = forEachSystem ({ pkgs, ... }: pkgs.nixfmt);

      devShells = forEachSystem (
        { pkgs, ... }:
        {
          default = pkgs.mkShell {
            packages = checkerPackages pkgs;

            shellHook = ''
              if [ -t 1 ]; then
                echo "YAMAI verification shell: Quint $(quint --version)"
                echo "TLC is available as: $(command -v tlc)"
                echo "Run: quint verify --main yamai_protocol --backend tlc --invariants protocol_invariant verification/quint/yamai_protocol.qnt"
              fi
            '';
          };
        }
      );

      checks = forEachSystem (
        { pkgs, ... }:
        let
          artifactValidatorCheck = pkgs.runCommand "yamai-artifact-validator" {
            src = ./.;
            nativeBuildInputs = [ pkgs.python3 ];
          } ''
            set -eu
            mkdir "$out"
            validator="$src/scripts/validate_artifacts.py"
            if [ ! -f "$validator" ]; then
              echo "scripts/validate_artifacts.py is not present" >&2
              exit 1
            fi
            cd "$src"
            python3 scripts/validate_artifacts.py > "$out/validate.log" 2>&1
            oracle="$src/scripts/score_oracle.py"
            if [ ! -f "$oracle" ]; then
              echo "scripts/score_oracle.py is not present" >&2
              exit 1
            fi
            python3 scripts/score_oracle.py > "$out/score-oracle.log" 2>&1
            echo "$validator" > "$out/validator"
            echo "$oracle" > "$out/scoring-oracle"
          '';

          toolchainCheck = pkgs.runCommand "yamai-quint-toolchain" {
            artifactDependency = artifactValidatorCheck;
            nativeBuildInputs = checkerPackages pkgs;
            tlcSmokeSpec = pkgs.writeText "YAMAI_TLC_Smoke.tla" ''
              ---- MODULE YAMAI_TLC_Smoke ----
              VARIABLE x
              Init == x = 0
              Next == x' = x
              Inv == x = 0
              ====
            '';
            tlcSmokeConfig = pkgs.writeText "YAMAI_TLC_Smoke.cfg" ''
              INIT Init
              NEXT Next
              INVARIANT Inv
            '';
          } ''
            set -eu
            mkdir "$out"
            test -d "$artifactDependency"
            quint --version > "$out/quint-version"
            command -v tlc > "$out/tlc-path"
            cp "$tlcSmokeSpec" "$TMPDIR/YAMAI_TLC_Smoke.tla"
            cp "$tlcSmokeConfig" "$TMPDIR/YAMAI_TLC_Smoke.cfg"
            (
              cd "$TMPDIR"
              tlc YAMAI_TLC_Smoke.tla > "$out/tlc-check.log" 2>&1
            )
            java -version > "$out/java-version" 2>&1
            z3 -version > "$out/z3-version"
          '';

          safetyCheck = pkgs.runCommand "yamai-quint-model" {
            src = ./.;
            toolchainDependency = toolchainCheck;
            nativeBuildInputs = checkerPackages pkgs;
          } ''
            set -eu
            mkdir "$out"
            test -d "$toolchainDependency"

            model_dir="$src/verification/quint"
            if [ ! -d "$model_dir" ]; then
              echo "verification/quint is not present" >&2
              exit 1
            fi

            models="$(find "$model_dir" -maxdepth 1 -type f -name '*.qnt' -print | sort)"
            if [ -z "$models" ]; then
              echo "verification/quint contains no .qnt model" >&2
              exit 1
            fi

            model="$model_dir/yamai_protocol.qnt"
            if [ ! -f "$model" ]; then
              echo "verification/quint/yamai_protocol.qnt is not present" >&2
              exit 1
            fi

            work_dir="$TMPDIR/verification/quint"
            mkdir -p "$work_dir"
            cp "$model_dir"/*.qnt "$work_dir"/
            model="$work_dir/yamai_protocol.qnt"
            cd "$TMPDIR"
            quint verify \
              --main yamai_protocol \
              --backend tlc \
              --invariants protocol_invariant \
              --verbosity 1 \
              "$model" > "$out/quint-verify.log" 2>&1
            echo "$model" > "$out/model"
          '';

          coreSafetyCheck = pkgs.runCommand "yamai-quint-protocol-core" {
            src = ./.;
            toolchainDependency = toolchainCheck;
            nativeBuildInputs = checkerPackages pkgs;
          } ''
            set -eu
            mkdir "$out"
            test -d "$toolchainDependency"

            model_dir="$src/verification/quint"
            model="$model_dir/yamai_protocol_core_bounded.qnt"
            if [ ! -f "$model" ]; then
              echo "verification/quint/yamai_protocol_core_bounded.qnt is not present" >&2
              exit 1
            fi

            work_dir="$TMPDIR/verification/quint-core"
            mkdir -p "$work_dir"
            cp "$model_dir"/*.qnt "$work_dir"/
            model="$work_dir/yamai_protocol_core_bounded.qnt"
            cd "$TMPDIR"
            quint parse "$model" > "$out/quint-parse.log" 2>&1
            quint typecheck "$model" > "$out/quint-typecheck.log" 2>&1
            quint run \
              --main yamai_protocol_core_bounded \
              --invariants protocol_invariant refinement_mapping \
              --witnesses witness_complete \
              --max-steps 24 \
              --max-samples 1 \
              --seed 0x79616d61695f636f \
              --verbosity 1 \
              "$model" > "$out/quint-bounded-safety.log" 2>&1
            if ! grep -Eq '^witness_complete was witnessed in [1-9][0-9]* trace' "$out/quint-bounded-safety.log"; then
              cat "$out/quint-bounded-safety.log" >&2
              exit 1
            fi
            echo "$model" > "$out/model"
          '';

          temporalCheck = pkgs.runCommand "yamai-quint-model-temporal" {
            src = ./.;
            safetyDependency = safetyCheck;
            nativeBuildInputs = checkerPackages pkgs;
          } ''
            set -eu
            mkdir "$out"
            test -d "$safetyDependency"

            model_dir="$src/verification/quint"
            if [ ! -d "$model_dir" ]; then
              echo "verification/quint is not present" >&2
              exit 1
            fi

            model="$model_dir/yamai_protocol.qnt"
            if [ ! -f "$model" ]; then
              echo "verification/quint/yamai_protocol.qnt is not present" >&2
              exit 1
            fi

            work_dir="$TMPDIR/verification/quint"
            mkdir -p "$work_dir"
            cp "$model_dir"/*.qnt "$work_dir"/
            model="$work_dir/yamai_protocol.qnt"
            cd "$TMPDIR"
            quint verify \
              --main yamai_protocol \
              --backend tlc \
              --temporal host_seq_bounded,ended_state_is_quiescent,group_resolves_under_weak_fairness,timeout_closes_under_weak_fairness,resume_returns_under_weak_fairness \
              --verbosity 1 \
              "$model" > "$out/quint-verify-temporal.log" 2>&1
            echo "$model" > "$out/model"
          '';

          witnessCheck = pkgs.runCommand "yamai-quint-model-witnesses" {
            src = ./.;
            temporalDependency = temporalCheck;
            nativeBuildInputs = checkerPackages pkgs;
          } ''
            set -eu
            mkdir "$out"
            test -d "$temporalDependency"

            model_dir="$src/verification/quint"
            if [ ! -d "$model_dir" ]; then
              echo "verification/quint is not present" >&2
              exit 1
            fi

            model="$model_dir/yamai_protocol.qnt"
            if [ ! -f "$model" ]; then
              echo "verification/quint/yamai_protocol.qnt is not present" >&2
              exit 1
            fi

            witnesses="witness_request_open witness_group_closed witness_defaulted witness_partial_default witness_late_ack_after_default witness_resumed witness_snapshot"
            work_dir="$TMPDIR/verification/quint"
            mkdir -p "$work_dir"
            cp "$model_dir"/*.qnt "$work_dir"/
            model="$work_dir/yamai_protocol.qnt"
            cd "$TMPDIR"
            witness_log="$out/quint-witness.log"
            quint run \
              --main yamai_protocol \
              --backend rust \
              --invariants protocol_invariant \
              --witnesses $witnesses \
              --max-steps 40 \
              --max-samples 100 \
              --seed 0x67816c9a00a64203 \
              --verbosity 1 \
              "$model" > "$witness_log" 2>&1

            for witness in $witnesses; do
              if ! grep -Eq "^$witness was witnessed in [1-9][0-9]* trace" "$witness_log"; then
                echo "$witness was not reached by the bounded simulation" >&2
                cat "$witness_log" >&2
                exit 1
              fi
            done

            echo "$model" > "$out/model"
          '';

          extendedParseTypecheckCheck = pkgs.runCommand "yamai-quint-model-extended-parse-typecheck" {
            src = ./.;
            previousDependency = witnessCheck;
            nativeBuildInputs = checkerPackages pkgs;
          } ''
            set -eu
            mkdir "$out"
            test -d "$previousDependency"

            model_dir="$src/verification/quint"
            model="$model_dir/yamai_protocol_extended.qnt"
            if [ ! -f "$model" ]; then
              echo "verification/quint/yamai_protocol_extended.qnt is not present" >&2
              exit 1
            fi

            work_dir="$TMPDIR/verification/quint-extended"
            mkdir -p "$work_dir"
            cp "$model_dir"/*.qnt "$work_dir"/
            model="$work_dir/yamai_protocol_extended.qnt"
            quint parse "$model" > "$out/quint-parse.log" 2>&1
            quint typecheck "$model" > "$out/quint-typecheck.log" 2>&1
            echo "$model" > "$out/model"
          '';

          extendedSafetyCheck = pkgs.runCommand "yamai-quint-model-extended" {
            src = ./.;
            parseTypecheckDependency = extendedParseTypecheckCheck;
            nativeBuildInputs = checkerPackages pkgs;
          } ''
            set -eu
            mkdir "$out"
            test -d "$parseTypecheckDependency"

            model_dir="$src/verification/quint"
            model="$model_dir/yamai_protocol_extended.qnt"
            if [ ! -f "$model" ]; then
              echo "verification/quint/yamai_protocol_extended.qnt is not present" >&2
              exit 1
            fi

            work_dir="$TMPDIR/verification/quint-extended"
            mkdir -p "$work_dir"
            cp "$model_dir"/*.qnt "$work_dir"/
            model="$work_dir/yamai_protocol_extended.qnt"
            cd "$TMPDIR"
            quint verify \
              --main yamai_protocol_extended \
              --backend tlc \
              --invariants protocol_invariant \
              --max-steps 40 \
              --verbosity 1 \
              "$model" > "$out/quint-verify.log" 2>&1
            echo "$model" > "$out/model"
          '';

          extendedWitnessCheck = pkgs.runCommand "yamai-quint-model-extended-witnesses" {
            src = ./.;
            safetyDependency = extendedSafetyCheck;
            nativeBuildInputs = checkerPackages pkgs;
          } ''
            set -eu
            mkdir "$out"
            test -d "$safetyDependency"

            model_dir="$src/verification/quint"
            model="$model_dir/yamai_protocol_extended.qnt"
            if [ ! -f "$model" ]; then
              echo "verification/quint/yamai_protocol_extended.qnt is not present" >&2
              exit 1
            fi

            witnesses="witness_negotiated_active witness_rejected_version witness_rejected_profile witness_rejected_capability witness_gap witness_duplicate witness_conflict witness_replayed witness_request_group witness_deadline_boundary witness_pending_resume witness_pending_snapshot witness_terminalized_end"
            work_dir="$TMPDIR/verification/quint-extended"
            mkdir -p "$work_dir"
            cp "$model_dir"/*.qnt "$work_dir"/
            model="$work_dir/yamai_protocol_extended.qnt"
            cd "$TMPDIR"
            witness_log="$out/quint-witness.log"
            quint run \
              --main yamai_protocol_extended \
              --backend rust \
              --invariants type_ok host_seq_non_decreasing negotiation_invariant \
              wire_invariant request_invariant resume_snapshot_invariant \
              resume_pending_invariant terminalization_invariant score_conservation \
              --witnesses $witnesses \
              --max-steps 32 \
              --max-samples 2000 \
              --seed 0x7a6d61695f657874 \
              --verbosity 1 \
              "$model" > "$witness_log" 2>&1

            for witness in $witnesses; do
              if ! grep -Eq "^$witness was witnessed in [1-9][0-9]* trace" "$witness_log"; then
                echo "$witness was not reached by the bounded simulation" >&2
                cat "$witness_log" >&2
                exit 1
              fi
            done

            echo "$model" > "$out/model"
          '';

          requestLivenessParseTypecheckCheck = pkgs.runCommand "yamai-quint-request-liveness-parse-typecheck" {
            src = ./.;
            previousDependency = extendedWitnessCheck;
            nativeBuildInputs = checkerPackages pkgs;
          } ''
            set -eu
            mkdir "$out"
            test -d "$previousDependency"

            model_dir="$src/verification/quint"
            model="$model_dir/yamai_request_liveness.qnt"
            if [ ! -f "$model" ]; then
              echo "verification/quint/yamai_request_liveness.qnt is not present" >&2
              exit 1
            fi

            work_dir="$TMPDIR/verification/quint-request-liveness"
            mkdir -p "$work_dir"
            cp "$model_dir"/*.qnt "$work_dir"/
            model="$work_dir/yamai_request_liveness.qnt"
            quint parse "$model" > "$out/quint-parse.log" 2>&1
            quint typecheck "$model" > "$out/quint-typecheck.log" 2>&1
            echo "$model" > "$out/model"
          '';

          requestLivenessSafetyCheck = pkgs.runCommand "yamai-quint-request-liveness" {
            src = ./.;
            parseTypecheckDependency = requestLivenessParseTypecheckCheck;
            nativeBuildInputs = checkerPackages pkgs;
          } ''
            set -eu
            mkdir "$out"
            test -d "$parseTypecheckDependency"

            model_dir="$src/verification/quint"
            model="$model_dir/yamai_request_liveness.qnt"
            if [ ! -f "$model" ]; then
              echo "verification/quint/yamai_request_liveness.qnt is not present" >&2
              exit 1
            fi

            work_dir="$TMPDIR/verification/quint-request-liveness"
            mkdir -p "$work_dir"
            cp "$model_dir"/*.qnt "$work_dir"/
            model="$work_dir/yamai_request_liveness.qnt"
            cd "$TMPDIR"
            quint verify \
              --main yamai_request_liveness \
              --backend tlc \
              --invariants protocol_invariant \
              --max-steps 40 \
              --verbosity 1 \
              "$model" > "$out/quint-verify.log" 2>&1
            echo "$model" > "$out/model"
          '';

          requestLivenessTemporalCheck = pkgs.runCommand "yamai-quint-request-liveness-temporal" {
            src = ./.;
            safetyDependency = requestLivenessSafetyCheck;
            nativeBuildInputs = checkerPackages pkgs;
          } ''
            set -eu
            mkdir "$out"
            test -d "$safetyDependency"

            model_dir="$src/verification/quint"
            model="$model_dir/yamai_request_liveness.qnt"
            if [ ! -f "$model" ]; then
              echo "verification/quint/yamai_request_liveness.qnt is not present" >&2
              exit 1
            fi

            work_dir="$TMPDIR/verification/quint-request-liveness"
            mkdir -p "$work_dir"
            cp "$model_dir"/*.qnt "$work_dir"/
            model="$work_dir/yamai_request_liveness.qnt"
            cd "$TMPDIR"
            quint verify \
              --main yamai_request_liveness \
              --backend tlc \
              --temporal group_resolves_under_stable_connection,timeout_closes_under_stable_connection,late_ack_survives_default_under_stable_connection \
              --max-steps 40 \
              --verbosity 1 \
              "$model" > "$out/quint-verify-temporal.log" 2>&1
            echo "$model" > "$out/model"
          '';

          requestLivenessWitnessCheck = pkgs.runCommand "yamai-quint-request-liveness-witnesses" {
            src = ./.;
            temporalDependency = requestLivenessTemporalCheck;
            nativeBuildInputs = checkerPackages pkgs;
          } ''
            set -eu
            mkdir "$out"
            test -d "$temporalDependency"

            model_dir="$src/verification/quint"
            model="$model_dir/yamai_request_liveness.qnt"
            if [ ! -f "$model" ]; then
              echo "verification/quint/yamai_request_liveness.qnt is not present" >&2
              exit 1
            fi

            witnesses="witness_single_open witness_group_open witness_group_closed witness_timeout_boundary witness_defaulted witness_late_ack_pending witness_late_ack_acked witness_single_resolved witness_group_resolved"
            work_dir="$TMPDIR/verification/quint-request-liveness"
            mkdir -p "$work_dir"
            cp "$model_dir"/*.qnt "$work_dir"/
            model="$work_dir/yamai_request_liveness.qnt"
            cd "$TMPDIR"
            witness_log="$out/quint-witness.log"
            quint run \
              --main yamai_request_liveness \
              --backend rust \
              --invariant true \
              --witnesses $witnesses \
              --max-steps 40 \
              --max-samples 1000 \
              --seed 0x7a6d6c697665 \
              --verbosity 1 \
              "$model" > "$witness_log" 2>&1

            for witness in $witnesses; do
              if ! grep -Eq "^$witness was witnessed in [1-9][0-9]* trace" "$witness_log"; then
                echo "$witness was not reached by the bounded simulation" >&2
                cat "$witness_log" >&2
                exit 1
              fi
            done

            echo "$model" > "$out/model"
          '';

          resumeDeliveryParseTypecheckCheck = pkgs.runCommand "yamai-quint-resume-delivery-parse-typecheck" {
            src = ./.;
            previousDependency = requestLivenessWitnessCheck;
            nativeBuildInputs = checkerPackages pkgs;
          } ''
            set -eu
            mkdir "$out"
            test -d "$previousDependency"

            model_dir="$src/verification/quint"
            model="$model_dir/yamai_resume_delivery.qnt"
            if [ ! -f "$model" ]; then
              echo "verification/quint/yamai_resume_delivery.qnt is not present" >&2
              exit 1
            fi

            work_dir="$TMPDIR/verification/quint-resume-delivery"
            mkdir -p "$work_dir"
            cp "$model_dir"/*.qnt "$work_dir"/
            model="$work_dir/yamai_resume_delivery.qnt"
            quint parse "$model" > "$out/quint-parse.log" 2>&1
            quint typecheck "$model" > "$out/quint-typecheck.log" 2>&1
            echo "$model" > "$out/model"
          '';

          resumeDeliverySafetyCheck = pkgs.runCommand "yamai-quint-resume-delivery" {
            src = ./.;
            parseTypecheckDependency = resumeDeliveryParseTypecheckCheck;
            nativeBuildInputs = checkerPackages pkgs;
          } ''
            set -eu
            mkdir "$out"
            test -d "$parseTypecheckDependency"

            model_dir="$src/verification/quint"
            model="$model_dir/yamai_resume_delivery.qnt"
            if [ ! -f "$model" ]; then
              echo "verification/quint/yamai_resume_delivery.qnt is not present" >&2
              exit 1
            fi

            work_dir="$TMPDIR/verification/quint-resume-delivery"
            mkdir -p "$work_dir"
            cp "$model_dir"/*.qnt "$work_dir"/
            model="$work_dir/yamai_resume_delivery.qnt"
            cd "$TMPDIR"
            quint verify \
              --main yamai_resume_delivery \
              --backend tlc \
              --invariants type_ok host_seq_non_decreasing backlog_invariant \
              gap_invariant request_invariant pending_saved_invariant \
              disconnected_internal_invariant protocol_invariant \
              --max-steps 24 \
              --verbosity 1 \
              "$model" > "$out/quint-verify.log" 2>&1
            echo "$model" > "$out/model"
          '';

          resumeDeliveryTemporalCheck = pkgs.runCommand "yamai-quint-resume-delivery-temporal" {
            src = ./.;
            safetyDependency = resumeDeliverySafetyCheck;
            nativeBuildInputs = checkerPackages pkgs;
          } ''
            set -eu
            mkdir "$out"
            test -d "$safetyDependency"

            model_dir="$src/verification/quint"
            model="$model_dir/yamai_resume_delivery.qnt"
            if [ ! -f "$model" ]; then
              echo "verification/quint/yamai_resume_delivery.qnt is not present" >&2
              exit 1
            fi

            work_dir="$TMPDIR/verification/quint-resume-delivery"
            mkdir -p "$work_dir"
            cp "$model_dir"/*.qnt "$work_dir"/
            model="$work_dir/yamai_resume_delivery.qnt"
            cd "$TMPDIR"
            quint verify \
              --main yamai_resume_delivery \
              --backend tlc \
              --temporal internal_terminalization_under_fairness,gap_replay_under_eventual_stable_connection,pending_delivery_under_eventual_stable_connection \
              --max-steps 24 \
              --verbosity 1 \
              "$model" > "$out/quint-verify-temporal.log" 2>&1
            echo "$model" > "$out/model"
          '';

          resumeDeliveryWitnessCheck = pkgs.runCommand "yamai-quint-resume-delivery-witnesses" {
            src = ./.;
            temporalDependency = resumeDeliveryTemporalCheck;
            nativeBuildInputs = checkerPackages pkgs;
          } ''
            set -eu
            mkdir "$out"
            test -d "$temporalDependency"

            model_dir="$src/verification/quint"
            model="$model_dir/yamai_resume_delivery.qnt"
            if [ ! -f "$model" ]; then
              echo "verification/quint/yamai_resume_delivery.qnt is not present" >&2
              exit 1
            fi

            witnesses="witness_disconnect_default witness_disconnect_resolve witness_reconnect_replay witness_snapshot_replacement witness_gap_recovery"
            work_dir="$TMPDIR/verification/quint-resume-delivery"
            mkdir -p "$work_dir"
            cp "$model_dir"/*.qnt "$work_dir"/
            model="$work_dir/yamai_resume_delivery.qnt"
            cd "$TMPDIR"
            witness_log="$out/quint-witness.log"
            quint run \
              --main yamai_resume_delivery \
              --backend rust \
              --invariants type_ok host_seq_non_decreasing backlog_invariant \
              gap_invariant request_invariant pending_saved_invariant \
              disconnected_internal_invariant protocol_invariant \
              --witnesses $witnesses \
              --max-steps 24 \
              --max-samples 5000 \
              --seed 0x7a6d61695f726573 \
              --verbosity 1 \
              "$model" > "$witness_log" 2>&1

            for witness in $witnesses; do
              if ! grep -Eq "^$witness was witnessed in [1-9][0-9]* trace" "$witness_log"; then
                echo "$witness was not reached by the bounded simulation" >&2
                cat "$witness_log" >&2
                exit 1
              fi
            done

            echo "$model" > "$out/model"
          '';
        in
        {
          quint-toolchain = toolchainCheck;
          artifact-validator = artifactValidatorCheck;
          quint-model = safetyCheck;
          quint-protocol-core = coreSafetyCheck;
          quint-model-temporal = temporalCheck;
          quint-model-witnesses = witnessCheck;
          quint-model-extended-parse-typecheck = extendedParseTypecheckCheck;
          quint-model-extended = extendedSafetyCheck;
          quint-model-extended-witnesses = extendedWitnessCheck;
          quint-request-liveness-parse-typecheck = requestLivenessParseTypecheckCheck;
          quint-request-liveness = requestLivenessSafetyCheck;
          quint-request-liveness-temporal = requestLivenessTemporalCheck;
          quint-request-liveness-witnesses = requestLivenessWitnessCheck;
          quint-resume-delivery-parse-typecheck = resumeDeliveryParseTypecheckCheck;
          quint-resume-delivery = resumeDeliverySafetyCheck;
          quint-resume-delivery-temporal = resumeDeliveryTemporalCheck;
          quint-resume-delivery-witnesses = resumeDeliveryWitnessCheck;
        }
      );
    };
}
