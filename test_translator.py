
import unittest
import json
from edi_translator import X12Parser, EDI850Mapper

SAMPLE_EDI = """ISA*00* *00* *ZZ*RETAILGIANT    *ZZ*TRANSACTGLOBAL *251024*1030*U*00401*000001005*0*P*>~
GS*PO*RETAILGIANT*TRANSACTGLOBAL*20251024*1030*1005*X*004010~
ST*850*0001~
BEG*00*SA*PO-99887766**20251024~
CUR*SE*USD~
REF*DP*055~
REF*VR*112233~
N1*ST*Seattle Distribution Center*92*0044~
N3*1234 Rainier Ave S*Suite 400~
N4*Seattle*WA*98144*US~
N1*BY*RetailGiant HQ*91*RG-HQ-01~
N3*5000 Commerce Blvd~
N4*New York*NY*10001*US~
PO1*1*100*EA*24.99**UP*123456789012*VN*TG-WIDGET-01*SK*SKU-555~
PID*F****Premium Blue Widget~
PO1*2*50*CA*120.00**UP*987654321098*VN*TG-GADGET-99~
PID*F****Bulk Gadget Pack~
CTT*2*150~
SE*15*0001~
GE*1*1005~
IEA*1*000001005~"""

class TestIDITranslator(unittest.TestCase):

    def test_parser_delimiters(self):
        parser = X12Parser(SAMPLE_EDI)
        self.assertEqual(parser.element_separator, '*')
        self.assertEqual(parser.segment_terminator, '~')
        
    def test_translation_structure(self):
        parser = X12Parser(SAMPLE_EDI)
        mapper = EDI850Mapper(parser)
        result = mapper.translate()
        
        # Helper to print failures
        if "errors" in result:
            print(result["errors"])
            
        # 1. Meta
        self.assertEqual(result['meta']['senderId'], 'RETAILGIANT')
        self.assertEqual(result['meta']['transactionType'], '850')
        
        # 2. Header
        self.assertEqual(result['order']['poNumber'], 'PO-99887766')
        self.assertEqual(result['order']['orderDate'], '2025-10-24')
        self.assertEqual(result['order']['vendorId'], '112233')
        
        # 3. Addresses
        self.assertEqual(result['order']['shipTo']['id'], '0044')
        self.assertEqual(result['order']['shipTo']['address']['line1'], '1234 Rainier Ave S')
        self.assertEqual(result['order']['shipTo']['address']['line2'], 'Suite 400')
        self.assertEqual(result['order']['buyer']['id'], 'RG-HQ-01')
        
        # 4. Lines
        self.assertEqual(len(result['order']['lines']), 2)
        
        line1 = result['order']['lines'][0]
        self.assertEqual(line1['lineNumber'], 1)
        self.assertEqual(line1['quantity'], 100)
        self.assertEqual(line1['unitPrice'], 24.99)
        self.assertEqual(line1['upc'], '123456789012')
        self.assertEqual(line1['description'], 'Premium Blue Widget')
        
        line2 = result['order']['lines'][1]
        self.assertEqual(line2['quantity'], 50)
        self.assertEqual(line2['uom'], 'CASE')
        
        # 5. Summary
        self.assertEqual(result['meta']['validation']['lineCount'], 2)

if __name__ == '__main__':
    unittest.main()
