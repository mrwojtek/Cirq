# Copyright 2021 The Cirq Developers
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from typing import Optional, Tuple

from unittest import mock
import itertools
import numpy as np
import pytest

import cirq
import cirq.google.calibration.workflow as workflow

from cirq.google.calibration.phased_fsim import (
    FloquetPhasedFSimCalibrationOptions,
    FloquetPhasedFSimCalibrationRequest,
    PhasedFSimCharacterization,
    PhasedFSimCalibrationResult,
    WITHOUT_CHI_FLOQUET_PHASED_FSIM_CHARACTERIZATION,
)


SQRT_ISWAP_PARAMETERS = cirq.google.PhasedFSimCharacterization(
    theta=np.pi / 4, zeta=0.0, chi=0.0, gamma=0.0, phi=0.0
)
SQRT_ISWAP_GATE = cirq.FSimGate(np.pi / 4, 0.0)


def _fsim_identity_converter(gate: cirq.Gate) -> Optional[Tuple[cirq.FSimGate, float]]:
    if isinstance(gate, cirq.FSimGate):
        return gate, 0.0
    return None


def test_make_floquet_request_for_moment_none_for_measurements() -> None:
    a, b, c, d = cirq.LineQubit.range(4)
    moment = cirq.Moment(cirq.measure(a, b, c, d))
    assert (
        workflow.make_floquet_request_for_moment(
            moment, WITHOUT_CHI_FLOQUET_PHASED_FSIM_CHARACTERIZATION
        )
        is None
    )


def test_make_floquet_request_for_moment_fails_for_non_gate_operation() -> None:
    moment = cirq.Moment(cirq.GlobalPhaseOperation(coefficient=1.0))
    with pytest.raises(workflow.IncompatibleMomentError):
        workflow.make_floquet_request_for_moment(
            moment, WITHOUT_CHI_FLOQUET_PHASED_FSIM_CHARACTERIZATION
        )


def test_make_floquet_request_for_moment_fails_for_unsupported_gate() -> None:
    a, b = cirq.LineQubit.range(2)
    moment = cirq.Moment(cirq.CZ(a, b))
    with pytest.raises(workflow.IncompatibleMomentError):
        workflow.make_floquet_request_for_moment(
            moment,
            WITHOUT_CHI_FLOQUET_PHASED_FSIM_CHARACTERIZATION,
            gates_translator=_fsim_identity_converter,
        )


def test_make_floquet_request_for_moment_fails_for_mixed_gates() -> None:
    a, b, c, d = cirq.LineQubit.range(4)
    moment = cirq.Moment(
        [
            cirq.FSimGate(theta=np.pi / 4, phi=0.0).on(a, b),
            cirq.FSimGate(theta=np.pi / 8, phi=0.0).on(c, d),
        ]
    )
    with pytest.raises(workflow.IncompatibleMomentError):
        workflow.make_floquet_request_for_moment(
            moment,
            WITHOUT_CHI_FLOQUET_PHASED_FSIM_CHARACTERIZATION,
            gates_translator=_fsim_identity_converter,
        )


def test_make_floquet_request_for_moment_fails_for_mixed_moment() -> None:
    a, b, c = cirq.LineQubit.range(3)
    moment = cirq.Moment([cirq.FSimGate(theta=np.pi / 4, phi=0.0).on(a, b), cirq.Z.on(c)])
    with pytest.raises(workflow.IncompatibleMomentError):
        workflow.make_floquet_request_for_moment(
            moment, WITHOUT_CHI_FLOQUET_PHASED_FSIM_CHARACTERIZATION
        )


def test_make_floquet_request_for_circuit() -> None:
    a, b, c, d = cirq.LineQubit.range(4)
    circuit = cirq.Circuit(
        [
            [cirq.X(a), cirq.Y(c)],
            [cirq.FSimGate(np.pi / 4, 0.0).on(a, b), cirq.FSimGate(np.pi / 4, 0.0).on(c, d)],
            [cirq.FSimGate(np.pi / 4, 0.0).on(b, c)],
            [cirq.WaitGate(duration=cirq.Duration(micros=5.0)).on(b)],
        ]
    )
    options = WITHOUT_CHI_FLOQUET_PHASED_FSIM_CHARACTERIZATION

    requests, mapping = workflow.make_floquet_request_for_circuit(circuit, options=options)

    assert requests == [
        cirq.google.calibration.FloquetPhasedFSimCalibrationRequest(
            pairs=((a, b), (c, d)), gate=SQRT_ISWAP_GATE, options=options
        ),
        cirq.google.calibration.FloquetPhasedFSimCalibrationRequest(
            pairs=((b, c),), gate=SQRT_ISWAP_GATE, options=options
        ),
    ]
    assert mapping == [None, 0, 1, None]


def test_make_floquet_request_for_circuit_merges_sub_sets() -> None:
    a, b, c, d, e = cirq.LineQubit.range(5)
    circuit = cirq.Circuit(
        [
            [cirq.X(a), cirq.Y(c)],
            [cirq.FSimGate(np.pi / 4, 0.0).on(a, b), cirq.FSimGate(np.pi / 4, 0.0).on(c, d)],
            [cirq.FSimGate(np.pi / 4, 0.0).on(b, c)],
            [cirq.FSimGate(np.pi / 4, 0.0).on(a, b)],
        ]
    )
    circuit += cirq.Moment(
        [cirq.FSimGate(np.pi / 4, 0.0).on(b, c), cirq.FSimGate(np.pi / 4, 0.0).on(d, e)]
    )
    options = WITHOUT_CHI_FLOQUET_PHASED_FSIM_CHARACTERIZATION

    requests, mapping = workflow.make_floquet_request_for_circuit(circuit, options=options)

    assert requests == [
        cirq.google.calibration.FloquetPhasedFSimCalibrationRequest(
            pairs=((a, b), (c, d)), gate=SQRT_ISWAP_GATE, options=options
        ),
        cirq.google.calibration.FloquetPhasedFSimCalibrationRequest(
            pairs=((b, c), (d, e)), gate=SQRT_ISWAP_GATE, options=options
        ),
    ]
    assert mapping == [None, 0, 1, 0, 1]


def test_make_floquet_request_for_circuit_merges_many_circuits() -> None:
    options = WITHOUT_CHI_FLOQUET_PHASED_FSIM_CHARACTERIZATION
    a, b, c, d, e = cirq.LineQubit.range(5)

    circuit_1 = cirq.Circuit(
        [
            [cirq.X(a), cirq.Y(c)],
            [cirq.FSimGate(np.pi / 4, 0.0).on(a, b), cirq.FSimGate(np.pi / 4, 0.0).on(c, d)],
            [cirq.FSimGate(np.pi / 4, 0.0).on(b, c)],
            [cirq.FSimGate(np.pi / 4, 0.0).on(a, b)],
        ]
    )

    requests_1, mapping_1 = workflow.make_floquet_request_for_circuit(circuit_1, options=options)

    assert requests_1 == [
        cirq.google.calibration.FloquetPhasedFSimCalibrationRequest(
            pairs=((a, b), (c, d)), gate=SQRT_ISWAP_GATE, options=options
        ),
        cirq.google.calibration.FloquetPhasedFSimCalibrationRequest(
            pairs=((b, c),), gate=SQRT_ISWAP_GATE, options=options
        ),
    ]
    assert mapping_1 == [None, 0, 1, 0]

    circuit_2 = cirq.Circuit(
        [cirq.FSimGate(np.pi / 4, 0.0).on(b, c), cirq.FSimGate(np.pi / 4, 0.0).on(d, e)]
    )

    requests_2, mapping_2 = workflow.make_floquet_request_for_circuit(
        circuit_2, options=options, initial=requests_1
    )
    print(requests_2)

    assert requests_2 == [
        cirq.google.calibration.FloquetPhasedFSimCalibrationRequest(
            pairs=((a, b), (c, d)), gate=SQRT_ISWAP_GATE, options=options
        ),
        cirq.google.calibration.FloquetPhasedFSimCalibrationRequest(
            pairs=((b, c), (d, e)), gate=SQRT_ISWAP_GATE, options=options
        ),
    ]
    assert mapping_2 == [1]


def test_make_floquet_request_for_circuit_does_not_merge_sub_sets_when_disabled() -> None:
    a, b, c, d, e = cirq.LineQubit.range(5)
    circuit = cirq.Circuit(
        [
            [cirq.X(a), cirq.Y(c)],
            [cirq.FSimGate(np.pi / 4, 0.0).on(a, b), cirq.FSimGate(np.pi / 4, 0.0).on(c, d)],
            [cirq.FSimGate(np.pi / 4, 0.0).on(b, c)],
            [cirq.FSimGate(np.pi / 4, 0.0).on(a, b)],
        ]
    )
    circuit += cirq.Circuit(
        [cirq.FSimGate(np.pi / 4, 0.0).on(b, c), cirq.FSimGate(np.pi / 4, 0.0).on(d, e)],
        [cirq.FSimGate(np.pi / 4, 0.0).on(b, c)],
    )
    options = WITHOUT_CHI_FLOQUET_PHASED_FSIM_CHARACTERIZATION

    requests, mapping = workflow.make_floquet_request_for_circuit(
        circuit, options=options, merge_subsets=False
    )

    assert requests == [
        cirq.google.calibration.FloquetPhasedFSimCalibrationRequest(
            pairs=((a, b), (c, d)), gate=SQRT_ISWAP_GATE, options=options
        ),
        cirq.google.calibration.FloquetPhasedFSimCalibrationRequest(
            pairs=((b, c),), gate=SQRT_ISWAP_GATE, options=options
        ),
        cirq.google.calibration.FloquetPhasedFSimCalibrationRequest(
            pairs=((a, b),), gate=SQRT_ISWAP_GATE, options=options
        ),
        cirq.google.calibration.FloquetPhasedFSimCalibrationRequest(
            pairs=((b, c), (d, e)), gate=SQRT_ISWAP_GATE, options=options
        ),
    ]
    assert mapping == [None, 0, 1, 2, 3, 1]


def test_make_floquet_request_for_circuit_merges_compatible_sets() -> None:
    a, b, c, d, e, f = cirq.LineQubit.range(6)
    circuit = cirq.Circuit([cirq.X(a), cirq.Y(c)])
    circuit += cirq.Moment([cirq.FSimGate(np.pi / 4, 0.0).on(a, b)])
    circuit += cirq.Moment(
        [cirq.FSimGate(np.pi / 4, 0.0).on(b, c), cirq.FSimGate(np.pi / 4, 0.0).on(d, e)]
    )
    circuit += cirq.Moment([cirq.FSimGate(np.pi / 4, 0.0).on(c, d)])
    circuit += cirq.Moment(
        [cirq.FSimGate(np.pi / 4, 0.0).on(a, f), cirq.FSimGate(np.pi / 4, 0.0).on(d, e)]
    )
    options = WITHOUT_CHI_FLOQUET_PHASED_FSIM_CHARACTERIZATION

    requests, mapping = workflow.make_floquet_request_for_circuit(circuit, options=options)

    assert requests == [
        cirq.google.calibration.FloquetPhasedFSimCalibrationRequest(
            pairs=((a, b), (c, d)), gate=SQRT_ISWAP_GATE, options=options
        ),
        cirq.google.calibration.FloquetPhasedFSimCalibrationRequest(
            pairs=((a, f), (b, c), (d, e)), gate=SQRT_ISWAP_GATE, options=options
        ),
    ]
    assert mapping == [None, 0, 1, 0, 1]


def test_run_characterization_empty():
    assert workflow.run_characterizations([], None, 'qproc', cirq.google.FSIM_GATESET) == []


def test_run_characterization_fails_when_invalid_arguments():
    with pytest.raises(ValueError):
        assert workflow.run_characterizations(
            [], None, 'qproc', cirq.google.FSIM_GATESET, max_layers_per_request=0
        )


def test_run_characterization():
    q_00, q_01, q_02, q_03 = [cirq.GridQubit(0, index) for index in range(4)]
    gate = cirq.FSimGate(theta=np.pi / 4, phi=0.0)

    request = FloquetPhasedFSimCalibrationRequest(
        gate=gate,
        pairs=((q_00, q_01), (q_02, q_03)),
        options=FloquetPhasedFSimCalibrationOptions(
            characterize_theta=True,
            characterize_zeta=True,
            characterize_chi=False,
            characterize_gamma=False,
            characterize_phi=True,
        ),
    )

    result = cirq.google.CalibrationResult(
        code=cirq.google.api.v2.calibration_pb2.SUCCESS,
        error_message=None,
        token=None,
        valid_until=None,
        metrics=cirq.google.Calibration(
            cirq.google.api.v2.metrics_pb2.MetricsSnapshot(
                metrics=[
                    cirq.google.api.v2.metrics_pb2.Metric(
                        name='angles',
                        targets=[
                            '0_qubit_a',
                            '0_qubit_b',
                            '0_theta_est',
                            '0_zeta_est',
                            '0_phi_est',
                            '1_qubit_a',
                            '1_qubit_b',
                            '1_theta_est',
                            '1_zeta_est',
                            '1_phi_est',
                        ],
                        values=[
                            cirq.google.api.v2.metrics_pb2.Value(str_val='0_0'),
                            cirq.google.api.v2.metrics_pb2.Value(str_val='0_1'),
                            cirq.google.api.v2.metrics_pb2.Value(double_val=0.1),
                            cirq.google.api.v2.metrics_pb2.Value(double_val=0.2),
                            cirq.google.api.v2.metrics_pb2.Value(double_val=0.3),
                            cirq.google.api.v2.metrics_pb2.Value(str_val='0_2'),
                            cirq.google.api.v2.metrics_pb2.Value(str_val='0_3'),
                            cirq.google.api.v2.metrics_pb2.Value(double_val=0.4),
                            cirq.google.api.v2.metrics_pb2.Value(double_val=0.5),
                            cirq.google.api.v2.metrics_pb2.Value(double_val=0.6),
                        ],
                    )
                ]
            )
        ),
    )

    job = cirq.google.engine.EngineJob('', '', '', None)
    job._calibration_results = [result]

    engine = mock.MagicMock(spec=cirq.google.Engine)
    engine.run_calibration.return_value = job

    progress_calls = []

    def progress(step: int, steps: int) -> None:
        progress_calls.append((step, steps))

    actual = workflow.run_characterizations(
        [request], engine, 'qproc', cirq.google.FSIM_GATESET, progress_func=progress
    )

    expected = [
        PhasedFSimCalibrationResult(
            parameters={
                (q_00, q_01): PhasedFSimCharacterization(
                    theta=0.1, zeta=0.2, chi=None, gamma=None, phi=0.3
                ),
                (q_02, q_03): PhasedFSimCharacterization(
                    theta=0.4, zeta=0.5, chi=None, gamma=None, phi=0.6
                ),
            },
            gate=gate,
            options=FloquetPhasedFSimCalibrationOptions(
                characterize_theta=True,
                characterize_zeta=True,
                characterize_chi=False,
                characterize_gamma=False,
                characterize_phi=True,
            ),
        )
    ]

    assert actual == expected
    assert progress_calls == [(1, 1)]


def test_run_floquet_characterization_for_circuit():
    q_00, q_01, q_02, q_03 = [cirq.GridQubit(0, index) for index in range(4)]
    gate = cirq.FSimGate(theta=np.pi / 4, phi=0.0)

    circuit = cirq.Circuit([gate.on(q_00, q_01), gate.on(q_02, q_03)])

    options = FloquetPhasedFSimCalibrationOptions(
        characterize_theta=True,
        characterize_zeta=True,
        characterize_chi=False,
        characterize_gamma=False,
        characterize_phi=True,
    )

    job = cirq.google.engine.EngineJob('', '', '', None)
    job._calibration_results = [
        cirq.google.CalibrationResult(
            code=cirq.google.api.v2.calibration_pb2.SUCCESS,
            error_message=None,
            token=None,
            valid_until=None,
            metrics=cirq.google.Calibration(
                cirq.google.api.v2.metrics_pb2.MetricsSnapshot(
                    metrics=[
                        cirq.google.api.v2.metrics_pb2.Metric(
                            name='angles',
                            targets=[
                                '0_qubit_a',
                                '0_qubit_b',
                                '0_theta_est',
                                '0_zeta_est',
                                '0_phi_est',
                                '1_qubit_a',
                                '1_qubit_b',
                                '1_theta_est',
                                '1_zeta_est',
                                '1_phi_est',
                            ],
                            values=[
                                cirq.google.api.v2.metrics_pb2.Value(str_val='0_0'),
                                cirq.google.api.v2.metrics_pb2.Value(str_val='0_1'),
                                cirq.google.api.v2.metrics_pb2.Value(double_val=0.1),
                                cirq.google.api.v2.metrics_pb2.Value(double_val=0.2),
                                cirq.google.api.v2.metrics_pb2.Value(double_val=0.3),
                                cirq.google.api.v2.metrics_pb2.Value(str_val='0_2'),
                                cirq.google.api.v2.metrics_pb2.Value(str_val='0_3'),
                                cirq.google.api.v2.metrics_pb2.Value(double_val=0.4),
                                cirq.google.api.v2.metrics_pb2.Value(double_val=0.5),
                                cirq.google.api.v2.metrics_pb2.Value(double_val=0.6),
                            ],
                        )
                    ]
                )
            ),
        )
    ]

    engine = mock.MagicMock(spec=cirq.google.Engine)
    engine.run_calibration.return_value = job

    characterizations, mapping = workflow.run_floquet_characterization_for_circuit(
        circuit, engine, 'qproc', cirq.google.FSIM_GATESET, options=options
    )

    assert characterizations == [
        PhasedFSimCalibrationResult(
            parameters={
                (q_00, q_01): PhasedFSimCharacterization(
                    theta=0.1, zeta=0.2, chi=None, gamma=None, phi=0.3
                ),
                (q_02, q_03): PhasedFSimCharacterization(
                    theta=0.4, zeta=0.5, chi=None, gamma=None, phi=0.6
                ),
            },
            gate=gate,
            options=options,
        )
    ]
    assert mapping == [0]


@pytest.mark.parametrize(
    'theta,zeta,chi,gamma,phi',
    itertools.product([0.1, 0.7], [-0.3, 0.1, 0.5], [-0.3, 0.2, 0.4], [-0.6, 0.1, 0.6], [0.2, 0.6]),
)
def test_create_corrected_fsim_gate(
    theta: float, zeta: float, chi: float, gamma: float, phi: float
) -> None:
    a, b = cirq.LineQubit.range(2)

    expected_gate = cirq.PhasedFSimGate(theta=theta, zeta=-zeta, chi=-chi, gamma=-gamma, phi=phi)
    expected = cirq.unitary(expected_gate)

    corrected_gate, corrected_mapping = workflow.create_corrected_fsim_gate(
        (a, b),
        cirq.FSimGate(theta=theta, phi=phi),
        cirq.google.PhasedFSimCharacterization(
            theta=theta, zeta=zeta, chi=chi, gamma=gamma, phi=phi
        ),
        phase_exponent=0.0,
        characterization_index=5,
    )
    actual = cirq.unitary(cirq.Circuit(corrected_gate))

    assert cirq.equal_up_to_global_phase(actual, expected)
    assert corrected_mapping == [None, 5, None]


# def test_create_corrected_fsim_gate_when_phase_exponent() -> None:
#     a, b = cirq.LineQubit.range(2)
#     theta = np.pi / 4
#
#     expected_gate = cirq.PhasedFSimGate(theta=-theta)
#     expected = cirq.unitary(expected_gate)
#
#     corrected_gate, corrected_mapping = workflow.create_corrected_fsim_gate(
#         (a, b),
#         cirq.FSimGate(theta=theta, phi=0.0),
#         cirq.google.PhasedFSimCharacterization(
#             theta=theta, zeta=0.0, chi=0.0, gamma=0.0, phi=0.0
#         ),
#         phase_exponent=0.5,
#         characterization_index=5,
#     )
#     actual = cirq.unitary(cirq.Circuit(corrected_gate))
#     print(actual)
#     print(expected)
#
#     assert cirq.equal_up_to_global_phase(actual, expected)
#     assert corrected_mapping == [None, 5, None]


@pytest.mark.parametrize(
    'theta,zeta,chi,gamma,phi',
    itertools.product(
        [np.pi / 4, -0.2], [-0.3, 0.1, 0.5], [-0.3, 0.2, 0.4], [-0.6, 0.1, 0.6], [0.2, 0.6]
    ),
)
def test_create_corrected_fsim_gate_when_phase_exponent(
    theta: float, zeta: float, chi: float, gamma: float, phi: float
) -> None:
    a, b = cirq.LineQubit.range(2)

    expected_gate = cirq.PhasedFSimGate(theta=-theta, zeta=-zeta, chi=-chi, gamma=-gamma, phi=phi)
    expected = cirq.unitary(expected_gate)

    corrected_gate, corrected_mapping = workflow.create_corrected_fsim_gate(
        (a, b),
        cirq.FSimGate(theta=theta, phi=phi),
        cirq.google.PhasedFSimCharacterization(
            theta=theta, zeta=zeta, chi=chi, gamma=gamma, phi=phi
        ),
        phase_exponent=0.5,
        characterization_index=5,
    )
    actual = cirq.unitary(cirq.Circuit(corrected_gate))

    assert cirq.equal_up_to_global_phase(actual, expected)
    assert corrected_mapping == [None, 5, None]


def test_run_floquet_calibration() -> None:
    parameters_ab = cirq.google.PhasedFSimCharacterization(zeta=0.5, chi=0.4, gamma=0.3)
    parameters_bc = cirq.google.PhasedFSimCharacterization(zeta=-0.5, chi=-0.4, gamma=-0.3)
    parameters_cd = cirq.google.PhasedFSimCharacterization(zeta=0.2, chi=0.3, gamma=0.4)

    a, b, c, d = cirq.LineQubit.range(4)
    engine_simulator = cirq.google.PhasedFSimEngineSimulator.create_from_dictionary_sqrt_iswap(
        parameters={
            (a, b): parameters_ab.merge_with(SQRT_ISWAP_PARAMETERS),
            (b, c): parameters_bc.merge_with(SQRT_ISWAP_PARAMETERS),
            (c, d): parameters_cd.merge_with(SQRT_ISWAP_PARAMETERS),
        }
    )

    circuit = cirq.Circuit(
        [
            [cirq.X(a), cirq.Y(c)],
            [cirq.FSimGate(np.pi / 4, 0.0).on(a, b), cirq.FSimGate(np.pi / 4, 0.0).on(c, d)],
            [cirq.FSimGate(np.pi / 4, 0.0).on(b, c)],
        ]
    )

    options = cirq.google.FloquetPhasedFSimCalibrationOptions(
        characterize_theta=False,
        characterize_zeta=True,
        characterize_chi=True,
        characterize_gamma=True,
        characterize_phi=False,
    )

    (
        calibrated,
        calibrations,
        mapping,
        calibrated_parameters,
    ) = workflow.run_floquet_phased_calibration_for_circuit(
        circuit,
        engine_simulator,
        processor_id=None,
        gate_set=cirq.google.SQRT_ISWAP_GATESET,
        options=options,
    )

    assert cirq.allclose_up_to_global_phase(
        engine_simulator.final_state_vector(calibrated), cirq.final_state_vector(circuit)
    )
    assert calibrations == [
        cirq.google.PhasedFSimCalibrationResult(
            gate=cirq.FSimGate(np.pi / 4, 0.0),
            parameters={(a, b): parameters_ab, (c, d): parameters_cd},
            options=options,
        ),
        cirq.google.PhasedFSimCalibrationResult(
            gate=cirq.FSimGate(np.pi / 4, 0.0), parameters={(b, c): parameters_bc}, options=options
        ),
    ]
    assert mapping == [None, None, 0, None, None, 1, None]
    assert calibrated_parameters == cirq.google.PhasedFSimCharacterization(
        zeta=0.0, chi=0.0, gamma=0.0
    )


# TODO: Check if calibration preserves moments.


def test_run_floquet_calibration_no_chi() -> None:
    parameters_ab = cirq.google.PhasedFSimCharacterization(theta=np.pi / 4, zeta=0.5, gamma=0.3)
    parameters_bc = cirq.google.PhasedFSimCharacterization(theta=np.pi / 4, zeta=-0.5, gamma=-0.3)
    parameters_cd = cirq.google.PhasedFSimCharacterization(theta=np.pi / 4, zeta=0.2, gamma=0.4)

    a, b, c, d = cirq.LineQubit.range(4)
    engine_simulator = cirq.google.PhasedFSimEngineSimulator.create_from_dictionary_sqrt_iswap(
        parameters={(a, b): parameters_ab, (b, c): parameters_bc, (c, d): parameters_cd},
        ideal_when_missing_parameter=True,
    )

    circuit = cirq.Circuit(
        [
            [cirq.X(a), cirq.Y(c)],
            [cirq.FSimGate(np.pi / 4, 0.0).on(a, b), cirq.FSimGate(np.pi / 4, 0.0).on(c, d)],
            [cirq.FSimGate(np.pi / 4, 0.0).on(b, c)],
        ]
    )

    calibrated, *_ = workflow.run_floquet_phased_calibration_for_circuit(
        circuit, engine_simulator, processor_id=None, gate_set=cirq.google.SQRT_ISWAP_GATESET
    )

    assert cirq.allclose_up_to_global_phase(
        engine_simulator.final_state_vector(calibrated), cirq.final_state_vector(circuit)
    )
