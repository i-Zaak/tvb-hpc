import os
import logging
logging.basicConfig(level=getattr(logging, os.environ.get('TVB_LOG', 'WARNING')))


from unittest import TestCase
import ctypes as ct

from tvb_hpc.codegen import BaseSpec, CfunGen1
from tvb_hpc.codegen.model import ModelGen1
from tvb_hpc.compiler import Compiler
from tvb_hpc.model import (BaseModel, _TestModel, HMJE, RWW, JansenRit,
                           Linear, G2DO)
from tvb_hpc.bold import BalloonWindkessel
from tvb_hpc.schemes import euler_maruyama_logp
from tvb_hpc.coupling import BaseCoupling


LOG = logging.getLogger(__name__)


class TestLogProb(TestCase):

    def setUp(self):
        self.model = _TestModel()

    def test_partials(self):
        logp = euler_maruyama_logp(
            self.model.state_sym,
            self.model.drift_sym,
            self.model.diffs_sym).sum()
        for var, expr in zip(self.model.indvars,
                             self.model.partial(logp)):
            pass


class TestModel(TestCase):

    def setUp(self):
        self.spec = BaseSpec('float', 8).dict

    def _build_func(self, model: BaseModel, spec, log_code=False):
        comp = Compiler()
        cg = ModelGen1(model)
        code = cg.generate_code(spec)
        if log_code:
            LOG.debug(code)
        lib = comp(model.__class__.__name__, code)
        fn = getattr(lib, cg.kernel_name)
        fn.restype = None  # equiv. C void return type
        ui = ct.c_uint
        f = {'float': ct.c_float, 'double': ct.c_double}[spec['float']]
        fp = ct.POINTER(f)
        fn.argtypes = [ui, fp, fp, fp, fp, fp, fp]
        return fn

    def _call(self, fn, *args):
        x, i, p, f, g, o = args
        nn = ct.c_uint(x.shape[0])
        fp = fn.argtypes[1]
        args = [a.ctypes.data_as(fp) for a in args]
        fn(nn, *args)

    def test_test_model_code_gen(self):
        model = _TestModel()
        fn = self._build_func(model, self.spec)
        arrs = model.prep_arrays(1024, self.spec)
        self._call(fn, *arrs)
        # TODO timing
        # TODO allclose against TVB

    def test_balloon_model(self):
        model = BalloonWindkessel()
        fn = self._build_func(model, self.spec)

    def test_hmje(self):
        model = HMJE()
        fn = self._build_func(model, self.spec)

    def test_rww(self):
        model = RWW()
        fn = self._build_func(model, self.spec)

    def test_jr(self):
        model = JansenRit()
        fn = self._build_func(model, self.spec)

    def test_linear(self):
        model = Linear()
        fn = self._build_func(model, self.spec)

    def test_g2do(self):
        model = G2DO()
        fn = self._build_func(model, self.spec)


class TestRNG(TestCase):

    def test_r123_unifrom(self):
        import numpy as np
        from tvb_hpc.rng import RNG
        from scipy.stats import kstest
        from tvb_hpc.compiler import CppCompiler
        from tvb_hpc.codegen import BaseSpec
        comp = CppCompiler(gen_asm=True)
        rng = RNG(comp)
        rng.build(BaseSpec())
        # LOG.debug(list(comp.cache.values())[0]['asm'])
        array = np.zeros((1024 * 1024, ), np.float32)
        rng.fill(array)
        d, p = kstest(array, 'norm')
        # check normal samples are normal
        self.assertAlmostEqual(array.mean(), 0, places=2)
        self.assertAlmostEqual(array.std(), 1, places=2)
        self.assertLess(d, 0.01)

class TestCoupling(TestCase):

    def setUp(self):
        self.spec = BaseSpec('float', 8)

    def _test_cfun_code(self, cf: BaseCoupling):
        comp = Compiler()
        cg = CfunGen1(cf)
        dll = comp(cf.__class__.__name__, cg.generate_code(self.spec))
        getattr(dll, cg.kernel_name_post_sum)
        getattr(dll, cg.kernel_name_pre_sum)

    def test_linear(self):
        from tvb_hpc.coupling import Linear
        from tvb_hpc.model import G2DO
        model = G2DO()
        self._test_cfun_code(Linear(model))

    def test_diff(self):
        from tvb_hpc.coupling import Diff
        from tvb_hpc.model import G2DO
        model = G2DO()
        self._test_cfun_code(Diff(model))

    def test_sigm(self):
        from tvb_hpc.coupling import Sigmoidal
        from tvb_hpc.model import JansenRit
        model = JansenRit()
        self._test_cfun_code(Sigmoidal(model))

    def test_kura(self):
        from tvb_hpc.coupling import Kuramoto as KCf
        from tvb_hpc.model import Kuramoto
        model = Kuramoto()
        self._test_cfun_code(KCf(model))


class TestNetwork(TestCase):

    def setUp(self):
        self.spec = BaseSpec('float', 8)
        self.comp = Compiler()

        from tvb_hpc.coupling import Sigmoidal
        from tvb_hpc.model import JansenRit
        self.model = JansenRit()
        self.cfun = Sigmoidal(self.model)


    def test_dense(self):
        from tvb_hpc.network import DenseNetwork
        from tvb_hpc.codegen import CfunGen1
        from tvb_hpc.codegen.network import NetGen1
        net = DenseNetwork(self.model, self.cfun)
        cg = NetGen1(net)
        code = cg.generate_code(CfunGen1(self.cfun), self.spec)
        dll = self.comp('dense_net', code)
        getattr(dll, cg.kernel_name)
