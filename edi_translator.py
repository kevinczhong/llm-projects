import json
import re
from datetime import datetime
from typing import List, Dict, Optional, Any

class X12Parser:
    """Parses X12 EDI files with dynamic delimiter detection."""
    
    def __init__(self, content: str):
        self.raw_content = content.strip()
        self.segment_terminator = '~'
        self.element_separator = '*'
        self.sub_element_separator = '>'
        self.segments = []
        
        self._parse()
        
    def _parse(self):
        # 1. Detect delimiters from ISA segment
        if self.raw_content.startswith("ISA"):
            # ISA segment look like: ISA*00*...
            # The char at index 3 is usually the element separator
            self.element_separator = self.raw_content[3]
            
            # Detect terminator
            # Strategy: If newlines are present, the terminator is the last non-newline char of the first line.
            # If no newlines, fall back to fixed width 106 or counting separators.
            
            first_line_end = self.raw_content.find('\n')
            if first_line_end != -1:
                # Use the end of the first line
                # Handle \r\n
                line = self.raw_content[:first_line_end].strip()
                self.segment_terminator = line[-1]
                # Sub-element is the one before terminator
                self.sub_element_separator = line[-3] # ISA...*P*>~ -> > is at -2? No: *>~. > is -2 if ~ is -1.
                # Let's count elements to be safe.
                parts = line.split(self.element_separator)
                if len(parts) >= 17: # ISA + 16 fields
                    # The last field is the sub-element separator?
                    # ISA*...*P*>~
                    # Split by '*': ['ISA', '00', ..., 'P', '>\x7e'] ? No.
                    # If sep is *, split gives:
                    # ISA*00*...*P*>~
                    # parts: 'ISA', '00', ..., 'P', '>~'
                    last_part = parts[-1] 
                    # last_part is '>~' (if no spaces).
                    if len(last_part) >= 2:
                        self.segment_terminator = last_part[-1]
                        self.sub_element_separator = last_part[0] # Usually just one char
            else:
                # Fallback to fixed width if no newline found (streaming)
                # But check length first
                if len(self.raw_content) > 106:
                     # Attempt standard 106
                    isa_block = self.raw_content[:106]
                    self.segment_terminator = isa_block[105]
                    self.sub_element_separator = isa_block[104]
                else: 
                     # Malformed or short? Try to find first occurrence of typical terminators (~, \, etc)
                     # Dangerous guess work. Let's assume ~ if strictly following sample.
                     pass
            
        # 2. Split into segments
        # Handle cases where newlines follow terminators
        raw_segments = self.raw_content.split(self.segment_terminator)
        
        for raw_seg in raw_segments:
            raw_seg = raw_seg.strip()
            if not raw_seg:
                continue
            
            elements = raw_seg.split(self.element_separator)
            tag = elements[0]
            
            self.segments.append({
                'tag': tag,
                'elements': elements, 
                'raw': raw_seg
            })

    def get_segments(self, tag: str) -> List[Dict]:
        """Return all segments matching tag."""
        return [s for s in self.segments if s['tag'] == tag]
        
    def get_segment(self, tag: str) -> Optional[Dict]:
        """Return first segment matching tag."""
        matches = self.get_segments(tag)
        return matches[0] if matches else None


class EDI850Mapper:
    """Maps X12 850 Purchase Order to OMS JSON."""
    
    def __init__(self, parser: X12Parser):
        self.parser = parser
        self.data = {}
        self.errors = []
        
    def translate(self) -> Dict:
        """Execute full translation."""
        try:
            self._map_meta()
            self._map_order_header()
            self._map_addresses()
            self._map_lines()
            self._map_summary()
            
            # Final structure
            payload = {
                "meta": self.data.get("meta", {}),
                "order": self.data.get("order", {}),
                "shipping": self.data.get("shipping", {}),
            }
            
            # Clean up empty objects
            if not payload["shipping"]:
                del payload["shipping"]
                
            return payload
            
        except Exception as e:
            self.errors.append(f"Translation error: {str(e)}")
            return {"errors": self.errors}

    def _get_element(self, segment: Dict, index: int, default: str = None) -> str:
        """Safely get element by index (0-based, matches X12 index since tag is 0)."""
        if segment and len(segment['elements']) > index:
            val = segment['elements'][index].strip()
            return val if val else default
        return default

    def _format_date(self, ccyymmdd: str) -> str:
        """Convert CCYYMMDD to YYYY-MM-DD."""
        if not ccyymmdd or len(ccyymmdd) != 8:
            return ccyymmdd
        try:
            dt = datetime.strptime(ccyymmdd, "%Y%m%d")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return ccyymmdd

    def _map_meta(self):
        """Map Header Control Segments (ISA, GS, ST)."""
        meta = {}
        
        # ISA
        isa = self.parser.get_segment("ISA")
        if isa:
            meta["senderId"] = self._get_element(isa, 6)
            meta["receiverId"] = self._get_element(isa, 8)
            meta["interchangeControlNumber"] = self._get_element(isa, 13)
            
        # GS
        gs = self.parser.get_segment("GS")
        if gs:
            meta["groupControlNumber"] = self._get_element(gs, 6)
            meta["ediVersion"] = self._get_element(gs, 8)
            
        # ST
        st = self.parser.get_segment("ST")
        if st:
            meta["transactionType"] = self._get_element(st, 1, "850") # Hardcode 850 per spec
            meta["transactionControlNumber"] = self._get_element(st, 2)
            
        self.data["meta"] = meta

    def _map_order_header(self):
        """Map BEG, CUR, REF, FOB."""
        order = {}
        
        # BEG
        beg = self.parser.get_segment("BEG")
        if beg:
            purpose_map = {'00': 'ORIGINAL', '01': 'CANCELLATION', '07': 'DUPLICATE'}
            type_map = {'DS': 'DROPSHIP', 'SA': 'STANDALONE', 'NE': 'NEW_ORDER'}
            
            order["type"] = purpose_map.get(self._get_element(beg, 1), self._get_element(beg, 1))
            order["category"] = type_map.get(self._get_element(beg, 2), self._get_element(beg, 2))
            order["poNumber"] = self._get_element(beg, 3)
            order["orderDate"] = self._format_date(self._get_element(beg, 5))
            
        # CUR
        cur = self.parser.get_segment("CUR")
        order["currencyCode"] = self._get_element(cur, 2, "USD")
        
        # REF
        refs = self.parser.get_segments("REF")
        custom_attrs = []
        for ref in refs:
            qual = self._get_element(ref, 1)
            val = self._get_element(ref, 2)
            
            if qual == 'VR': order["vendorId"] = val
            elif qual == 'DP': order["departmentId"] = val
            elif qual == 'PD': order["promotionId"] = val
            elif qual == 'IA': order["internalVendorId"] = val
            elif qual == 'ZZ': custom_attrs.append({'key': 'MutuallyDefined', 'value': val})
            
        if custom_attrs:
            order["customAttributes"] = custom_attrs
            
        # PER (Contact) - Spec says PER02 -> contact.name
        # Spec logic for PER is a bit ambiguous if it's header level. assuming Header level PER.
        # "4.7 PER" is in Header section.
        per = self.parser.get_segment("PER")
        if per:
            contact = {"name": self._get_element(per, 2)}
            comm_qual = self._get_element(per, 3)
            comm_val = self._get_element(per, 4)
            if comm_qual == 'TE': contact['phone'] = comm_val
            elif comm_qual == 'EM': contact['email'] = comm_val
            order["contact"] = contact
            
        # SAC (Header Charges)
        sacs = self.parser.get_segments("SAC")
        # Need to differentiate header SAC from Detail SAC.
        # X12Parser flattens segments, so we need to be careful.
        # "4.9 SAC – Allowance / Charge (Header)" vs "5.4 SAC"
        # Since we just have a list of segments, header SACs usually appear before the first PO1 loop.
        
        # Simple heuristic: SACs before first PO1 are header.
        header_sacs = []
        for seg in self.parser.segments:
            if seg['tag'] == 'PO1': break
            if seg['tag'] == 'SAC': header_sacs.append(seg)
            
        charges = []
        for sac in header_sacs:
            ind = self._get_element(sac, 1)
            type_map = {'A': 'ALLOWANCE', 'C': 'CHARGE'}
            method_map = {'02': 'OFF_INVOICE', '06': 'CHARGE_TO_BE_PAID'}
            
            charges.append({
                "type": type_map.get(ind, ind),
                "code": self._get_element(sac, 2),
                "amount": float(self._get_element(sac, 5, "0")),
                "method": method_map.get(self._get_element(sac, 12), self._get_element(sac, 12)),
                "description": self._get_element(sac, 15)
            })
            
        if charges:
            order["chargesAndAllowances"] = charges
            
        self.data["order"] = order
        
        # FOB
        fob = self.parser.get_segment("FOB")
        if fob:
            shipping = {}
            pay_map = {'PP': 'PREPAID', 'CC': 'COLLECT'}
            shipping["paymentMethod"] = pay_map.get(self._get_element(fob, 1), self._get_element(fob, 1))
            shipping["fobPoint"] = self._get_element(fob, 2) # ORIGIN/DESTINATION maps directly? Spec implies logic but doesn't map codes. Assuming direct.
            self.data["shipping"] = shipping

    def _map_addresses(self):
        """Map N1 Loops (Header Level)."""
        # Logic: Iterate segments, find N1. Collect following N3/N4 until next N1 or other segment.
        # N1 loops terminate when a new N1 starts or a non-N1/N2/N3/N4 appearing (like PO1).
        
        current_n1 = None
        current_obj = {}
        
        # We need to scan segments again to handle stateful loops
        # Or better: Extract N1 loops specifically.
        
        # Helper to decide target key from N101
        n1_map = {
            'ST': 'shipTo',
            'BT': 'billTo',
            'VN': 'vendorDetails',
            'BY': 'buyer'
        }
        
        # State machine
        in_header = True
        
        for seg in self.parser.segments:
            if seg['tag'] == 'PO1':
                in_header = False
                break
                
            if not in_header: break
            
            tag = seg['tag']
            if tag == 'N1':
                # Save previous if exists
                if current_n1 and current_obj:
                    target_key = n1_map.get(current_n1)
                    if target_key:
                        self.data["order"][target_key] = current_obj
                
                # Start new
                current_n1 = self._get_element(seg, 1)
                current_obj = {
                    "name": self._get_element(seg, 2),
                    "id": self._get_element(seg, 4)
                }
                
            elif tag == 'N3' and current_n1:
                addr = {}
                addr["line1"] = self._get_element(seg, 1)
                l2 = self._get_element(seg, 2)
                if l2: addr["line2"] = l2
                current_obj["address"] = addr
                
            elif tag == 'N4' and current_n1:
                # Merge into address or root? Spec output Sample shows city/state/zip at root of object usually?
                # Spec 9.2 doesn't explicitly show address struct for ST/BY, just "Seattle Distribution Center".
                # But N3 mapping says "address.line1".
                # Let's assume standard object structure: name, id, address: { line1, line2, city, state, zip, country }
                
                if "address" not in current_obj: current_obj["address"] = {}
                current_obj["address"]["city"] = self._get_element(seg, 1)
                current_obj["address"]["state"] = self._get_element(seg, 2)
                current_obj["address"]["postalCode"] = self._get_element(seg, 3)
                current_obj["address"]["country"] = self._get_element(seg, 4)

        # Catch last one
        if current_n1 and current_obj:
            target_key = n1_map.get(current_n1)
            if target_key:
                self.data["order"][target_key] = current_obj

    def _map_lines(self):
        """Map PO1 Loops."""
        lines = []
        
        current_line = None
        
        # Find start of detail section
        in_detail = False
        
        for seg in self.parser.segments:
            tag = seg['tag']
            if tag == 'PO1':
                in_detail = True
                # Save previous line
                if current_line:
                    lines.append(current_line)
                
                # Start new line
                current_line = {
                    "lineNumber": int(self._get_element(seg, 1)),
                    "quantity": int(self._get_element(seg, 2)),
                    "uom": self._get_map_uom(self._get_element(seg, 3)),
                    "unitPrice": float(self._get_element(seg, 4)),
                    "lineCharges": []
                }
                
                # Map Pars (Product IDs)
                # Iterates pairs starting at index 6: (6,7), (8,9), ...
                # P0106 is Qualifier, PO107 is ID
                elements = seg['elements']
                idx = 6
                while idx < len(elements) - 1:
                    qual = elements[idx].strip()
                    val = elements[idx+1].strip()
                    self._map_product_id(current_line, qual, val)
                    idx += 2
                    
            elif in_detail:
                if tag in ['CTT', 'SE']: # End of detail
                    break
                    
                if current_line:
                    if tag == 'PID':
                        # Append to description
                        desc = self._get_element(seg, 5)
                        if desc:
                            curr_desc = current_line.get("description", "")
                            current_line["description"] = (curr_desc + " " + desc).strip()
                            
                    elif tag == 'PO4':
                        current_line["packSize"] = int(self._get_element(seg, 1, "0"))
                        current_line["innerPack"] = int(self._get_element(seg, 14, "0")) # PO414 is index 14
                        
                    elif tag == 'SAC':
                        # Line level SAC
                        ind = self._get_element(seg, 1)
                        type_map = {'A': 'ALLOWANCE', 'C': 'CHARGE'}
                        current_line["lineCharges"].append({
                            "type": type_map.get(ind, ind),
                            "amount": float(self._get_element(seg, 5, "0")),
                            "description": self._get_element(seg, 15)
                        })

        if current_line:
            lines.append(current_line)
            
        self.data["order"]["lines"] = lines

    def _get_map_uom(self, code: str) -> str:
        mapping = {'EA': 'EACH', 'CA': 'CASE', 'DZ': 'DOZEN', 'KG': 'KG'}
        return mapping.get(code, code)

    def _map_product_id(self, line: Dict, qual: str, val: str):
        mapping = {
            'UP': 'upc',
            'VN': 'vendorPartNumber',
            'BP': 'buyerPartNumber',
            'EN': 'ean'
        }
        key = mapping.get(qual)
        if key:
            line[key] = val

    def _map_summary(self):
        """Map CTT, AMT."""
        ctt = self.parser.get_segment("CTT")
        if ctt:
            # Validation meta
            if "validation" not in self.data["meta"]: 
                self.data["meta"]["validation"] = {}
                
            self.data["meta"]["validation"]["lineCount"] = int(self._get_element(ctt, 1))
            self.data["meta"]["validation"]["hashTotal"] = self._get_element(ctt, 2)
            
        amt = self.parser.get_segment("AMT") # Assuming just one or logic to find 'Total' AMT
        # Spec 6.2 just says AMT02 -> totalAmount. Usually logic depends on AMT01 qualifier.
        # Assuming only one AMT or specific qualifier isn't mentioned in basic table (often 'TT' for total).
        # Let's map first AMT found if any.
        if amt:
            self.data["order"]["totalAmount"] = float(self._get_element(amt, 2, "0"))


def main():
    import sys
    from pathlib import Path
    
    # Simple CLI
    if len(sys.argv) < 2:
        print("Usage: python edi_translator.py <edi_file>")
        # Default to header sample for dev
        input_file = "data/sample.edi" # We might create this
    else:
        input_file = sys.argv[1]

    path = Path(input_file)
    if not path.exists():
        # Fallback to creating sample from spec if not exists
        print(f"File {input_file} not found.")
        return

    content = path.read_text()
    
    parser = X12Parser(content)
    mapper = EDI850Mapper(parser)
    result = mapper.translate()
    
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
