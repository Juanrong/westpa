import os, sys
from optparse import OptionParser
import wemd
from wemd import Segment, WESimIter

from wemd.util.wetool import WECmdLineTool
from wemd.environment import *

class WEMDRunTool(WECmdLineTool):
    def __init__(self):
        super(WEMDRunTool,self).__init__()
        
    def run(self):
        parser = OptionParser(usage = '%prog [options]')
        self.add_rc_option(parser)
        (opts, args) = parser.parse_args()
        self.read_runtime_config(opts)
        for key in ('backend.driver',):
            self.runtime_config.require(key)

        if self.runtime_config['data.storage_engine'] == 'sql':
            from wemd.data_managers.sa_data_manager import init_schema
            init_schema()
            
        sim_manager = wemd.sim_managers.make_sim_manager(self.runtime_config)    
        sim_manager.restore_state()
        sim_manager.run()
        
if __name__ == '__main__':
    WEMDRunTool().run()

