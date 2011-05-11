from __future__ import division, print_function

import os, sys, traceback
from copy import copy

if sys.version_info[0] < 3 and sys.version_info[1] < 7:
    sys.stderr.write('wemd requires at least Python version 2.7\n')
    sys.exit(1)

import logging
log = logging.getLogger('wemd_cmd')

import wemd
import numpy, operator, itertools

def cmd_init(sim_manager, args, aux_args):
    if aux_args:
        log.error('unexpected command line argument(s) ignored: %r' % aux_args)
        sys.exit(os.EX_USAGE)
        
    # Create HDF5 data file
    sim_manager.load_data_manager()
    sim_manager.load_we_driver()
    sim_manager.runtime_config.require('data.h5file')
    h5file = sim_manager.runtime_config.get_path('data.h5file')
    if os.path.exists(h5file):
        if args.force:
            sys.stdout.write('Deleting existing HDF5 file %r.\n' % h5file)
            os.unlink(h5file)
        else:
            sys.stderr.write('HDF5 file %r already exists; exiting.\n' % h5file)
            sys.exit(os.EX_USAGE)
    
    sys.stdout.write('Creating HDF5 file %r.\n' % h5file)
    sim_manager.data_manager.prepare_backing()
    
    # Load system driver and report a few initial statistics
    sim_manager.load_system_driver()
    system = sim_manager.system
    region_set = system.region_set
    
    tiprob = 0.0
    trprob = 0.0
    sys.stdout.write('\nInitial state:\n')
    sys.stdout.write('{:<16} {:<12} {:<12} {}\n'.format('Label', 'Init. Prob.', 'Recyc. Prob.', 'Coordinates'))
    for istate in system.initial_states:
        sys.stdout.write('{istate.label:<16} {istate.initial_prob:<12g} {istate.recycle_prob:<12g} {pcoord!s:<52}\n'\
                         .format(istate=istate, pcoord=list(istate.pcoord)))
        tiprob += istate.initial_prob
        trprob += istate.recycle_prob

    MACHEPS = numpy.finfo(numpy.float64).eps
    if abs(1.0 - tiprob) > args.ptol:
        sys.stderr.write('Initial probabilities do not sum to one.')
        sys.exit(1)
    if abs(1.0 - trprob) > args.ptol:
        sys.stderr.write('Recycle probabilities do not sum to one.')
        sys.exit(1)
    
    # Create initial segments
    segments = []
    for (i_istate, istate) in enumerate(system.initial_states):
        # Skip microstates that are for recycling only
        if istate.initial_prob == 0.0: continue
        target_count = istate.bin.target_count
        for i in xrange(0, target_count):
            segment = wemd.Segment(pcoord = system.new_pcoord_array(),
                                   weight = istate.initial_prob / target_count,
                                   p_parent_id = -(i_istate+1),
                                   parent_ids = set([-(i_istate+1)]),
                                   status = wemd.Segment.SEG_STATUS_PREPARED)
            segment.pcoord[0] = istate.pcoord
            istate.bin.add(segment)
            segments.append(segment)
        sys.stdout.write('%d replicas from initial point %r\n' % (target_count,istate.label))

    iprobtot = region_set.weight
    all_bins = region_set.get_all_bins()
    bin_occupancies = numpy.array(map(operator.attrgetter('count'), all_bins))
    target_occupancies = numpy.array(map(operator.attrgetter('target_count'), all_bins))
    
    sys.stdout.write('''
Total bins:             {:d}
Initial particles:      {:d} in {:d} bins, total weight = {:g}
Total target particles: {:d}
'''.format(len(all_bins),
           sum(bin_occupancies), len(bin_occupancies[bin_occupancies > 0]), iprobtot, 
           sum(target_occupancies)))
    
    # The user-side check for this was above; this is an assertion that the above assignment to bins 
    # and division of probability is correct
    assert abs(sim_manager.system.region_set.weight - tiprob) < MACHEPS*sum(bin_occupancies)
    
    # Send the segments over to the data manager to commit to disk            
    sim_manager.data_manager.prepare_iteration(1, segments, system.pcoord_ndim, system.pcoord_len,
                                               system.pcoord_dtype)
    sim_manager.data_manager.flush_backing()

    sim_manager.system.region_set.clear()    
    sys.stdout.write('Simulation prepared.\n')
        
def cmd_run(sim_manager, args, aux_args):
    # Let the work manager parse any remaining command-line arguments
    sim_manager.load_work_manager()
    aux_args = sim_manager.work_manager.parse_aux_args(aux_args)

    if aux_args:
        log.warning('unexpected command line argument(s) ignored: %r' % aux_args)
    
    sim_manager.load_data_manager()
    sim_manager.data_manager.open_backing()
        
    sim_manager.load_system_driver()
    sim_manager.load_we_driver()
    sim_manager.load_propagator()
    
    try:
        rc = sim_manager.run()
    except Exception as run_exc:
        if log.isEnabledFor(logging.INFO):
            traceback.print_exc()
        
        try:
            sim_manager.work_manager.shutdown(1)
        except Exception as shutdown_exc:
            log.error('error shutting down worker(s): %s' % shutdown_exc)
            traceback.print_exc()
            
        # raise the (hopefully) original error
        raise
    else:
        sim_manager.work_manager.shutdown(0)
        sys.exit(rc)   

# Set up command-line argument parser    
parser = wemd.rc.common_arg_parser(prog='wemd')
subparsers = parser.add_subparsers()

parser_init =    subparsers.add_parser('init', help='initialize a new simulation')
parser_init.add_argument('--force', dest='force', action='store_true',
                         help='overwrite any existing simulation data')
parser_init.add_argument('--ptol', dest='ptol', type=float, default=1.0e-8,
                         help='tolerance for sum of initial/recycle probabilities (default: 1.0e-8)')
parser_init.set_defaults(func=cmd_init)

parser_run =     subparsers.add_parser('run', help='start/continue a simulation')
parser_run.add_argument('--oneseg', dest='only_one_segment', action='store_true',
                        help='only propagate one segment (useful for debugging problems in propagators)')
parser_run.add_argument('--work-manager', dest='work_manager_name', 
                        help='use the given work manager to propagate segments (e.g. serial, threads, processes, tcpip, zmq'
                            +' or name a Python class; default: threads)')
mode_group = parser_run.add_mutually_exclusive_group()
mode_group.add_argument('--master', '--server', dest='work_manager_mode', action='store_const', const='master',
                        help='For distributed work managers, run as a master.')
mode_group.add_argument('--worker', '--client', dest='work_manager_mode', action='store_const', const='worker',
                        help='For distributed work managers, run as a worker')
parser_run.set_defaults(func=cmd_run)

parser_status =  subparsers.add_parser('status', help='report simulation status')

# Parse command line arguments
(args, aux_args) = parser.parse_known_args()

# Configure logging
wemd.rc.config_logging(args)

# Read runtime configuration file and merge command line arguments in
runtime_config = wemd.rc.read_config(args.run_config_file)
runtime_config.update_from_object(args)

# Load SimManager
sim_manager = wemd.rc.load_sim_manager(runtime_config)

# Branch to appropriate function
wemd.rc.default_cmdline_dispatch(args.func, args=(sim_manager,args,aux_args), kwargs=None, cmdline_args=args, log=log)
