from typing import Callable, List, Optional, Tuple, Union, cast

from cirq.circuits import Circuit
from cirq.ops import (
    FSimGate,
    Gate,
    GateOperation,
    MeasurementGate,
    Moment,
    Operation,
    Qid,
    SingleQubitGate,
    rz
)
from cirq.google.calibration.engine_simulator import (
    PhasedFSimEngineSimulator
)
from cirq.google.calibration.phased_fsim import (
    FloquetPhasedFSimCalibrationOptions,
    FloquetPhasedFSimCalibrationRequest,
    IncompatibleMomentError,
    PhasedFSimCalibrationRequest,
    PhasedFSimCalibrationResult,
    PhasedFSimCharacterization,
    sqrt_iswap_gates_translator
)
from cirq.google.engine import Engine
from cirq.google.serializable_gate_set import SerializableGateSet
from itertools import zip_longest


def floquet_characterization_for_moment(
        moment: Moment,
        options: FloquetPhasedFSimCalibrationOptions,
        gate_set: SerializableGateSet,
        gates_translator: Callable[[Gate], Optional[FSimGate]] = sqrt_iswap_gates_translator,
        pairs_in_canonical_order: bool = False,
        pairs_sorted: bool = False
) -> Optional[FloquetPhasedFSimCalibrationRequest]:

    measurement = False
    single_qubit = False
    gate: Optional[FSimGate] = None
    pairs = []

    for op in moment:
        if not isinstance(op, GateOperation):
            raise IncompatibleMomentError(
                'Moment contains operation different than GateOperation')

        if isinstance(op.gate, MeasurementGate):
            measurement = True
        elif isinstance(op.gate, SingleQubitGate):
            single_qubit = True
        else:
            translated_gate = gates_translator(op.gate)
            if translated_gate is None:
                raise IncompatibleMomentError(
                    f'Moment {moment} contains unsupported non-single qubit operation {op}')
            elif gate is not None and gate != translated_gate:
                raise IncompatibleMomentError(
                    f'Moment {moment} contains operations resolved to two different gates {gate} '
                    f'and {translated_gate}')
            else:
                gate = translated_gate
            pair = cast(Tuple[Qid, Qid],
                        tuple(sorted(op.qubits) if pairs_in_canonical_order else op.qubits))
            pairs.append(pair)

    if gate is None:
        # Either empty, single-qubit or measurement moment.
        return None

    if gate is not None and (measurement or single_qubit):
        raise IncompatibleMomentError(f'Moment contains mixed two-qubit operations and '
                                      f'single-qubit operations or measurement operations.')

    return FloquetPhasedFSimCalibrationRequest(
        gate=gate,
        gate_set=gate_set,
        pairs=tuple(sorted(pairs) if pairs_sorted else pairs),
        options=options
    )


# TODO: Add support for ISWAP ** 0.5 as well.
# TODO: Add support for WaitGates
# TODO: Add support for CircuitOperations.
def floquet_characterization_for_circuit(
        circuit: Circuit,
        gate_set: SerializableGateSet,
        gates_translator: Callable[[Gate], Optional[FSimGate]] = sqrt_iswap_gates_translator,
        options: FloquetPhasedFSimCalibrationOptions = FloquetPhasedFSimCalibrationOptions.
            all_except_for_chi_options(),
        merge_sub_sets: bool = True,
        initial: Optional[
            Tuple[List[FloquetPhasedFSimCalibrationRequest], List[Optional[int]]]] = None
) -> Tuple[List[FloquetPhasedFSimCalibrationRequest], List[Optional[int]]]:
    """
    Returns:
        Tuple of:
          - list of calibration requests,
          - list of indices of the generated calibration requests for each
            moment in the supplied circuit. If None occurs at certain position,
            it means that the related moment was not recognized for calibration.
    """

    def append_if_missing(calibration: FloquetPhasedFSimCalibrationRequest) -> int:
        if calibration.pairs not in pairs_map:
            index = len(calibrations)
            calibrations.append(calibration)
            pairs_map[calibration.pairs] = index
            return index
        else:
            return pairs_map[calibration.pairs]

    def merge_into_calibrations(calibration: FloquetPhasedFSimCalibrationRequest) -> int:
        new_pairs = set(calibration.pairs)
        for index in pairs_map.values():
            assert calibration.gate == calibrations[index].gate
            assert calibration.gate_set == calibrations[index].gate_set
            assert calibration.options == calibrations[index].options
            existing_pairs = calibrations[index].pairs
            if new_pairs.issubset(existing_pairs):
                return index
            elif new_pairs.issuperset(existing_pairs):
                calibrations[index] = calibration
                return index
            else:
                new_qubit_pairs = calibration.qubit_pairs
                existing_qubit_pairs = calibrations[index].qubit_pairs
                if all((new_qubit_pairs[q] == existing_qubit_pairs[q]
                        for q in set(new_qubit_pairs.keys()).intersection(existing_qubit_pairs.keys()))):
                    calibrations[index] = FloquetPhasedFSimCalibrationRequest(
                        gate=calibration.gate,
                        gate_set=gate_set,
                        pairs=tuple(sorted(new_pairs.union(existing_pairs))),
                        options=options
                    )
                    return index

        index = len(calibrations)
        calibrations.append(calibration)
        pairs_map[calibration.pairs] = index
        return index

    if initial is None:
        calibrations = []
        moments_map = []
    else:
        calibrations, moments_map = initial

    pairs_map = {}

    for moment in circuit:
        calibration = floquet_characterization_for_moment(moment, options, gate_set, gates_translator,
                                                          pairs_in_canonical_order=True,
                                                          pairs_sorted=True)

        if calibration is not None:
            if merge_sub_sets:
                index = merge_into_calibrations(calibration)
            else:
                index = append_if_missing(calibration)
            moments_map.append(index)
        else:
            moments_map.append(None)

    return calibrations, moments_map


def run_characterizations(calibrations: List[PhasedFSimCalibrationRequest],
                          engine: Union[Engine, PhasedFSimEngineSimulator],
                          processor_id: Optional[str],
                          max_layers_per_request: int = 1,
                          progress_func: Optional[Callable[[int, int], None]] = None
                          ) -> List[PhasedFSimCalibrationResult]:
    """Runs calibration requests on the Engine.

    Args:
        calibrations: List of calibrations to perform described in a request object.
        engine: cirq.google.Engine object used for running the calibrations.
        processor_id: processor_id passed to engine.run_calibrations method.
        handler_name:

    Returns:
        List of PhasedFSimCalibrationResult for each requested calibration.
    """
    if max_layers_per_request < 1:
        raise ValueError(f'Miaximum number of layers pere request must be at least 1, '
                         f'{max_layers_per_request} given')

    if not calibrations:
        return []

    gate_sets = [calibration.gate_set for calibration in calibrations]
    gate_set = gate_sets[0]
    if not all(gate_set == other for other in gate_sets):
        raise ValueError('All calibrations that run together must be defined for a single gate set')

    if isinstance(engine, Engine):
        if processor_id is None:
            raise ValueError('Processor id must not be None for engine simulation')
        if handler_name is None:
            raise ValueError('Handler name must not be None for engine simulation')

        results = []

        requests = [
            [calibration.to_calibration_layer()
             for calibration in calibrations[offset:offset + max_layers_per_request]]
            for offset in range(0, len(calibrations), max_layers_per_request)
        ]

        for request in requests:
            job = engine.run_calibration(request,
                                         processor_id=processor_id,
                                         gate_set=gate_set)
            request_results = job.calibration_results()
            results += [calibration.parse_result(result)
                        for calibration, result in zip(calibrations, request_results)]
            if progress_func:
                progress_func(len(results), len(calibrations))

    elif isinstance(engine, PhasedFSimEngineSimulator):
        results = engine.get_calibrations(calibrations)
    else:
        raise ValueError(f'Unsupported engine type {type(engine)}')

    return results


def phased_calibration_for_circuit(
        circuit: Circuit,
        characterizations: List[PhasedFSimCalibrationResult],
        moments_mapping: List[Optional[int]],
        gates_translator: Callable[[Gate], Optional[FSimGate]] = sqrt_iswap_gates_translator
) -> Tuple[Circuit, List[Optional[int]]]:
    default_phases = PhasedFSimCharacterization(
        zeta=0.0,
        chi=0.0,
        gamma=0.0
    )

    compensated = Circuit()
    new_mapping = []
    for index, moment in enumerate(circuit):
        characterization_index = moments_mapping[index]
        if characterization_index is not None:
            parameters = characterizations[characterization_index]
        else:
            parameters = None

        decompositions = []
        other = []
        new_moment_mapping = None
        for op in moment:
            if not isinstance(op, GateOperation):
                raise IncompatibleMomentError(
                    'Moment contains operation different than GateOperation')

            if isinstance(op.gate, (MeasurementGate, SingleQubitGate)):
                other.append(op)
            else:
                if parameters is None:
                    raise ValueError(f'Missing characterization data for moment {moment}')
                translated_gate = gates_translator(op.gate)
                if translated_gate is None:
                    raise IncompatibleMomentError(
                        f'Moment {moment} contains unsupported non-single qubit operation {op}')
                a, b = op.qubits
                pair_parameters = parameters.get_parameters(a, b)
                pair_parameters = pair_parameters.merge_with(default_phases)
                decomposed, decomposed_mapping = create_corrected_fsim_gate(
                    (a, b), translated_gate, pair_parameters, characterization_index)
                decompositions.append(decomposed)

                if new_moment_mapping is None:
                    new_moment_mapping = decomposed_mapping
                elif new_moment_mapping != decomposed_mapping:
                    raise ValueError(f'Inconsistent decompositions with a moment {moment}')

        if other and decompositions:
            raise IncompatibleMomentError(f'Moment {moment} contains mixed operations')
        elif other:
            compensated += Moment(other)
            new_mapping.append(characterization_index)
        elif decompositions:
            for operations in zip_longest(*decompositions, fillvalue=()):
                compensated += Moment(operations)
            new_mapping += new_moment_mapping

    return compensated, new_mapping


def create_corrected_fsim_gate(
        qubits: Tuple[Qid, Qid],
        gate: FSimGate,
        parameters: PhasedFSimCharacterization,
        characterization_index
) -> Tuple[Tuple[Tuple[Operation, ...], ...], List[Optional[int]]]:
    zeta = parameters.zeta
    gamma = parameters.gamma
    chi = parameters.chi

    a, b = qubits
    alpha = 0.5 * (zeta + chi)
    beta = 0.5 * (zeta - chi)
    return (
        (
            (rz(0.5 * gamma - alpha).on(a), rz(0.5 * gamma + alpha).on(b)),
            (gate.on(a, b),),
            (rz(0.5 * gamma - beta).on(a), rz(0.5 * gamma + beta).on(b))
        ),
        [
            None,
            characterization_index,
            None
        ]
    )


def run_floquet_characterization_for_circuit(
        circuit: Circuit,
        engine: Union[Engine, PhasedFSimEngineSimulator],
        processor_id: str,
        gate_set: SerializableGateSet,
        gates_translator: Callable[[Gate], Optional[FSimGate]] = sqrt_iswap_gates_translator,
        options: FloquetPhasedFSimCalibrationOptions = FloquetPhasedFSimCalibrationOptions.
            all_except_for_chi_options(),
        merge_sub_sets: bool = True,
        max_layers_per_request: int = 1,
        progress_func: Optional[Callable[[int, int], None]] = None
) -> Tuple[List[PhasedFSimCalibrationResult], List[Optional[int]]]:
    requests, mapping = floquet_characterization_for_circuit(
        circuit, gate_set, gates_translator, options, merge_sub_sets=merge_sub_sets)
    results = run_characterizations(
        requests,
        engine,
        processor_id,
        max_layers_per_request=max_layers_per_request,
        progress_func=progress_func
    )
    return results, mapping


def run_floquet_phased_calibration_for_circuit(
        circuit: Circuit,
        engine: Union[Engine, PhasedFSimEngineSimulator],
        processor_id: Optional[str],
        gate_set: SerializableGateSet,
        gates_translator: Callable[[Gate], Optional[FSimGate]] = sqrt_iswap_gates_translator,
        options: FloquetPhasedFSimCalibrationOptions = FloquetPhasedFSimCalibrationOptions(
            characterize_theta=False,
            characterize_zeta=True,
            characterize_chi=False,
            characterize_gamma=True,
            characterize_phi=False
        ),
        merge_sub_sets: bool = True,
        max_layers_per_request: int = 1,
        progress_func: Optional[Callable[[int, int], None]] = None
) -> Tuple[Circuit, List[PhasedFSimCalibrationResult], List[Optional[int]], PhasedFSimCharacterization]:
    requests, mapping = floquet_characterization_for_circuit(
        circuit, gate_set, gates_translator, options, merge_sub_sets=merge_sub_sets)
    characterizations = run_characterizations(
        requests,
        engine,
        processor_id,
        max_layers_per_request=max_layers_per_request,
        progress_func=progress_func
    )
    calibrated_circuit, calibrated_mapping = phased_calibration_for_circuit(
        circuit,
        characterizations,
        mapping,
        gates_translator
    )
    override = PhasedFSimCharacterization(
        zeta=0.0 if options.characterize_zeta else None,
        chi=0.0 if options.characterize_chi else None,
        gamma=0.0 if options.characterize_gamma else None
    )
    return calibrated_circuit, characterizations, calibrated_mapping, override
